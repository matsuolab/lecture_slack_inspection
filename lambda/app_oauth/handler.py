import os
import html
import base64
import hashlib
import hmac
import html
import json
import time
import secrets

from typing import Any
from urllib.parse import urlencode

import requests

from common.secret_manager import get_secret, put_secure_parameter,get_secret_no_cache
from common.observability import build_context, log_info, log_error

SERVICE = "app_oauth"

def _redirect(location: str) -> dict:
    return {
        "statusCode": 302,
        "headers": {"Location": location},
        "body": "",
    }

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")

def _b64url_decode(data: str) -> bytes:
    return base64.urlsafe_64decode(data + "==" * (-len(data) % 4))

def _oauth_redirect_uri() -> str:
    v = os.getenv("SLACK_OAUTH_REDIRECT_URI", "").strip()
    if not v:
        raise ValueError("SLACK_OAUTH_REDIRECT_URI environment variable is not set")
    return v

def _oauth_state_secret() -> str:
    v = get_secret("OAUTH_STATE_SECRET_PARAM_NAME")
    if not v:
        raise ValueError("OAUTH_STATE_SECRET_PARAM_NAME secret is not set")
    return v

def _allowed_team_ids() -> set[str]:
    raw = (get_secret_no_cache("OAUTH_ALLOWED_TEAM_IDS") or "").strip()
    if not raw:
        return set()
    # JSON配列もしくはカンマ区切りで指定できるようにする
    if raw.startswith("["):
        try:
            values = json.load(raw)
            return {str.strip() for x in values if str(x).strip()}
        except Exception:
            pass
        
    return {x.strip() for x in raw.split(",") if x.strip()}

def _bot_scopes() -> str:
    v = os.getenv("SLACK_BOT_SCOPES", "").strip()
    if not v:
        raise ValueError("SLACK_BOT_SCOPES environment variable is not set")
    return v

def _sign_state(payload: dict[str, Any]) -> str:
    body = _b64url_encode(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    )
    sig = _b64url_encode(
        hmac.new(_oauth_state_secret().encode("utf-8"), body.encode("utf-8"), hashlib.sha256).digest()
    )
    return f"{body}.{sig}"

def _verify_state(state: str, max_age_seconds: int = 600) -> dict[str, Any]:
    try:
        body, sig = state.rsplit(".", 1)
    except ValueError:
        raise ValueError("Invalid state format")
    
    expected = _b64url_encode(
        hmac.new(_oauth_state_secret().encode("utf-8"), body.encode("utf-8"), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(sig, expected):
        raise ValueError("Invalid state signature")

    payload = json.loads(_b64url_decode(body).decode("utf-8"))
    issued_at = int(payload["iat"])
    if int(time.time()) - issued_at > max_age_seconds:
        raise ValueError("State has expired")
    return payload

def _revoke_token(token: str) -> None:
    if not token:
        return
    try:
        requests.post(
            "https://slack.com/api/auth.revoke",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        ).raise_for_status()
    except Exception as e:
        # 握りつぶすが、ログには残す
        log_error(build_context({}, {}, service=SERVICE), action="token_revoke_failed", error=e)

def _handle_start(event: dict[str, Any], context: Any) -> dict:
    qs = event.get("queryStringParameters") or {}
    team_hint = (qs.get("team") or "").strip() or None
    
    allowed = _allowed_team_ids()
    if team_hint and allowed and team_hint not in allowed:
        return _html(403, "Installation is not allowed for this workspace")
    
    client_id = get_secret("SLACK_CLIENT_ID_PARAM_NAME")
    if not client_id:
        return _html(500, "Missing Slack client ID")
    
    state = _sign_state(
        {
            "iat": int(time.time()),
            "nonce": secrets.token_urlsafe(16),
            "team": team_hint,
        }
    )

    params = {
        "client_id": client_id,
        "scope": _bot_scopes(),
        "redirect_uri": _oauth_redirect_uri(),
        "state": state,
    }
    if team_hint:
        params["team"] = team_hint

    return _redirect(f"https://slack.com/oauth/v2/authorize?{urlencode(params)}")

def _handle_callback(event: dict[str, Any], context: Any) -> dict:
    qs = event.get("queryStringParameters") or {}

    if qs.get("error"):
        error = html.escape(str(qs["error"]))
        log_info(context, action="oauth_error", result="fail", slack_error=error)
        return _html(400, f"Slack OAuth failed\n\nError: {error}")
    
    code = qs.get("code")
    state = qs.get("state")
    if not code:
        return _html(400, "Missing OAuth code")
    if not state:
        return _html(400, "Missing state parameter")
    
    try:
        state_payload = _verify_state(state)
    except ValueError as e:
        log_info(context, action="oauth_error", result="fail", detail=str(e))
        return _html(400, "Invalid OAuth state")
    
    client_id = get_secret("SLACK_CLIENT_ID_PARAM_NAME")
    client_secret = get_secret("SLACK_CLIENT_SECRET_PARAM_NAME")
    prefix = os.getenv("SLACK_INSTALLATION_PARAM_PREFIX", "/slack/installation").rstrip("/")
    redirect_uri = _oauth_redirect_uri()
    if not client_id or not client_secret:
        return _html(500, "Server configuration error")
    
    resp = requests.post(
        "https://slack.com/api/oauth.v2.access",
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
        },
        timeout=10,
    )
    resp.raise_for_status()
    payload = resp.json()

    if not payload.get("ok"):
        slack_error = html.escape(str(payload.get("error", "unknown_error")))
        log_info(context, action="oauth_error", result="fail", slack_error=slack_error)
        return _html(400, f"Slack OAuth exchange failedError: {slack_error}")
    
    team = payload.get("team") or {}
    team_id = team.get("id")
    access_token = payload.get("access_token")
    incoming_webhook = payload.get("incoming_webhook") or {}
    alert_channel_id = incoming_webhook.get("channel_id")

    if not team_id or not access_token:
        log_info(context, action="oauth_payload_error", result="fail", payload=payload)
        return _html(500, "OAuth payload missing team_id or access_token")
    
    expected_team = state_payload.get("team")
    allowed = _allowed_team_ids()
    if allowed and team_id not in allowed:
        _revoke_token(access_token)
        log_info(context, action="oauth_rejected", team_id=team_id, allowed_teams=",".join(expected_team))
        return _html(403, "Installation is not allowed for this workspace")
    
    if expected_team and team_id != expected_team:
        _revoke_token(access_token)
        log_info(context, action="oauth_team_mismatch", actual_team=team_id, expected_team=expected_team)
        return _html(403, "OAuth Workspace mismatch")
    
    if not alert_channel_id:
        _revoke_token(access_token)
        return _html(400, "Missing alert channel information")
    
    put_secure_parameter(f"{prefix}/{team_id}/bot_token", access_token)
    put_secure_parameter(f"{prefix}/{team_id}/alert_channel_id", alert_channel_id)
    put_secure_parameter(f"{prefix}/{team_id}/installed_at", str(int(time.time())))
    log_info(context, action="oauth_install_saved", team_id=team_id, alert_channel_id=alert_channel_id)
    return _html(
        200,
        (
            "Slack app installation completed\n\n"
            f"team_id: {html.escape(team_id)}\n\n"
            f"alert_channel_id: {html.escape(alert_channel_id)}\n\n"
            "You can close this page."
        ),
    )

def _html(status_code: int, body: str) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "text/html", "charset": "utf-8"},
        "body": html.escape(body),
    }

def lambda_handler(event: dict[str, Any], context: Any) -> dict:
    context = build_context(event, context, service=SERVICE)
    log_info(context, action="oauth_request_received")

    try:
        path = (event.get("rawPath") or event.get("path") or "").rstrip("/")
        if path.endswith("/start"):
            return _handle_start(event, context)
        return _handle_callback(event, context)
    
    except Exception as e:
        log_error(context, action="oauth_callback_failed", error=e)
        return _html(500, "Internal Server Error")