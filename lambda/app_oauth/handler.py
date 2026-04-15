import os
import html
import base64
import hashlib
import hmac
import json
import time
import secrets
import re

from typing import Any
from urllib.parse import urlencode

import requests

from common.secret_manager import get_secret, put_secure_parameter, get_secret_no_cache
from common.observability import build_context, log_info, log_error

SERVICE = "app_oauth"


def _redirect(location: str) -> dict:
    return {
        "statusCode": 302,
        "headers": {"Location": location},
        "body": "",
    }


def _html(status_code: int, body: str) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "text/html", "charset": "utf-8"},
        "body": html.escape(body),
    }


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")


def _b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _header(event: dict[str, Any], name: str) -> str:
    headers = event.get("headers") or {}
    return (
        headers.get(name)
        or headers.get(name.lower())
        or headers.get(name.upper())
        or ""
    ).strip()


def _oauth_redirect_uri(event: dict[str, Any]) -> str:
    proto = _header(event, "X-Forwarded-Proto") or "https"
    host = _header(event, "X-Forwarded-Host") or _header(event, "Host")
    if not proto or not host:
        raise ValueError("Missing required headers for redirect URI")

    request_context = event.get("requestContext") or {}
    request_path = (
        event.get("rawPath")
        or request_context.get("path")
        or event.get("path")
        or ""
    ).strip()

    if request_path.endswith("/slack/oauth/start"):
        callback_path = request_path[:-len("/start")] + "/callback"
    elif request_path.endswith("/slack/oauth/callback"):
        callback_path = request_path
    else:
        stage = (request_context.get("stage") or "").strip()
        stage_prefix = f"/{stage}" if stage else ""
        callback_path = f"{stage_prefix}/slack/oauth/callback"

    return f"{proto}://{host}{callback_path}"


def _oauth_state_secret() -> str:
    v = get_secret("OAUTH_STATE_SECRET_PARAM_NAME")
    if not v:
        raise ValueError("OAUTH_STATE_SECRET_PARAM_NAME secret is not set")
    return v


def _allowed_team_ids() -> set[str]:
    raw = (get_secret_no_cache("OAUTH_ALLOWED_TEAM_IDS_PARAM_NAME") or "").strip()
    if not raw:
        return set()
    if raw.startswith("["):
        try:
            values = json.loads(raw)
            return {str(x).strip() for x in values if str(x).strip()}
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


def _channel_hint_from_query(qs: dict[str, Any]) -> str | None:
    raw = str(qs.get("channel") or "").strip()
    if not raw:
        return None
    if not re.fullmatch(r"[CG][A-Z0-9]+", raw):
        raise ValueError("Invalid channel parameter")
    return raw


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
        log_error(build_context({}, {}, service=SERVICE), action="token_revoke_failed", error=e)


def _send_test_notification(context: Any, access_token: str, channel_id: str) -> None:
    """通知チャンネルにテスト通知を送信（チャンネル設定の確認用）"""
    try:
        resp = requests.post(
            "https://slack.com/api/chat.postMessage",
            headers={"Authorization": f"Bearer {access_token}"},
            json={
                "channel": channel_id,
                "text": "✅ Botのインストールが完了しました。このチャンネルに違反通知が届きます。",
            },
            timeout=10,
        )
        if resp.ok and resp.json().get("ok"):
            log_info(context, action="oauth_test_notification_sent", channel=channel_id)
        else:
            log_error(context, action="oauth_test_notification_failed",
                      error=Exception(resp.json().get("error", "unknown")))
    except Exception as e:
        log_error(context, action="oauth_test_notification_failed", error=e)


def _save_installation(context: Any, prefix: str, team_id: str,
                       access_token: str, alert_channel_id: str | None) -> None:
    """OAuthトークンとインストール情報をSSMに保存"""
    put_secure_parameter(f"{prefix}/{team_id}/bot_token", access_token)
    if alert_channel_id:
        put_secure_parameter(f"{prefix}/{team_id}/alert_channel_id", alert_channel_id)
    put_secure_parameter(f"{prefix}/{team_id}/installed_at", str(int(time.time())))
    log_info(context, action="oauth_install_saved", team_id=team_id, alert_channel_id=alert_channel_id)

    if alert_channel_id:
        _send_test_notification(context, access_token, alert_channel_id)


def _handle_start(event: dict[str, Any], context: Any) -> dict:
    qs = event.get("queryStringParameters") or {}
    team_hint = (qs.get("team") or "").strip() or None
    try:
        channel_hint = _channel_hint_from_query(qs)
    except ValueError:
        return _html(400, "Invalid channel parameter.")

    allowed = _allowed_team_ids()
    if team_hint and allowed and team_hint not in allowed:
        return _html(403, "Installation is not allowed for this workspace")

    client_id = get_secret("SLACK_CLIENT_ID_PARAM_NAME")
    if not client_id:
        return _html(500, "Missing Slack client ID")
    redirect_uri = _oauth_redirect_uri(event)
    state = _sign_state(
        {
            "iat": int(time.time()),
            "nonce": secrets.token_urlsafe(16),
            "team": team_hint,
            "channel": channel_hint,
            "redirect_uri": redirect_uri,
        }
    )

    params = {
        "client_id": client_id,
        "scope": _bot_scopes(),
        "redirect_uri": redirect_uri,
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
    redirect_uri = (state_payload.get("redirect_uri") or "").strip()
    if not client_id or not client_secret:
        return _html(500, "Server configuration error")
    if not redirect_uri:
        return _html(400, "Missing redirect_uri in state")

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
        return _html(400, f"Slack OAuth exchange failed\n\nError: {slack_error}")

    team = payload.get("team") or {}
    team_id = team.get("id")
    access_token = payload.get("access_token")
    alert_channel_id = (state_payload.get("channel") or "").strip() or None

    if not team_id or not access_token:
        log_info(context, action="oauth_payload_error", result="fail", payload=payload)
        return _html(500, "OAuth payload missing team_id or access_token")

    expected_team = state_payload.get("team")
    allowed = _allowed_team_ids()
    if allowed and team_id not in allowed:
        _revoke_token(access_token)
        log_info(context,
                 action="oauth_rejected",
                 team_id=team_id,
                 expected_team=expected_team,
                 allowed_teams=",".join(sorted(allowed)),
                 )
        return _html(403, "Installation is not allowed for this workspace")

    if expected_team and team_id != expected_team:
        _revoke_token(access_token)
        log_info(context, action="oauth_team_mismatch", actual_team=team_id, expected_team=expected_team)
        return _html(403, "OAuth Workspace mismatch")

    _save_installation(context, prefix, team_id, access_token, alert_channel_id)

    body = [
        "Slack app installation completed",
        "",
        f"Team ID: {html.escape(team_id)}",
        "",
    ]
    if alert_channel_id:
        body.extend([
            f"alert_channel_id: {html.escape(alert_channel_id)}",
            "",
            "You can close this page.",
        ])
    else:
        body.extend([
            "alert_channel_id: (not configured)",
            "",
            "You can close this page, but configure alert_channel_id later before using alerts.",
        ])
    return _html(200, "\n".join(body))


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
