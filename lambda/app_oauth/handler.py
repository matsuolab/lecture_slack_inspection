import os
import html
from typing import Any

import requests

from common.secret_manager import get_secret, put_secure_parameter
from common.observability import build_context, log_info, log_error

SERVICE = "app_oauth"

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
        qs = event.get("queryStringParameters") or {}

        if qs.get("error"):
            error = html.escape(str(qs["error"]))
            log_info(context, action="oauth_error",result="fail", slack_error=error)
            return _html(400, f"<h1>Slack OAuth failed</h1><p>Error: {error}</p>")
        
        code = qs.get("code")
        if not code:
            log_info(context, action="oauth_error", result="fail", detail="missing code")
            return _html(400, "<h1>Slack OAuth failed</h1><p>Missing  OAuthcode</p>")
        
        client_id = get_secret("SLACK_CLIENT_ID_PARAM_NAME")
        client_secret = get_secret("SLACK_CLIENT_SECRET_PARAM_NAME")
        prefix = os.getenv("SLACK_INSTALLATION_PARAM_PREFIX", "/slack/installations").rstrip("/")

        if not client_id or not client_secret:
            log_info(context, action="oauth_error", result="fail", detail="missing client_id or client_secret")
            return _html(500, "<h1>Slack OAuth failed</h1><p>Server configuration error</p>")
        
        resp = requests.post(
            "https://slack.com/api/oauth.v2.access",
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=10,
        )
        resp.raise_for_status()
        payload = resp.json()

        if not payload.get("ok"):
            slack_error = html.escape(str(payload.get("error", "unknown_error")))
            log_info(context, action="oauth_error", result="fail", slack_error=slack_error)
            return _html(400, f"<h1>Slack OAuth exchange failed</h1><p>Error: {slack_error}</p>")
        
        team = payload.get("team") or {}
        team_id = team.get("id")
        access_token = payload.get("access_token")
        incoming_webhook = payload.get("incoming_webhook") or {}
        alert_channel_id = incoming_webhook.get("channel_id")

        if not team_id or not access_token:
            log_info(context, action="oauth_payload_error", result="fail", payload=payload)
            return _html(500, "<h1>OAuth payload missing team_id or access_token</h1>")
        
        put_secure_parameter(f"{prefix}/{team_id}/access_token", access_token)
        if alert_channel_id:
            put_secure_parameter(f"{prefix}/{team_id}/alert_channel_id", alert_channel_id)
            log_info(context, action="oauth_alert_channel_saved", team_id=team_id, channel_id=alert_channel_id)
        else:
            log_info(context, action="oauth_no_alert_channel", team_id=team_id)

        log_info(context, action="oauth_install_saved", team_id=team_id)

        return _html(
            200,
            (
                "<h1>Slack app installation completed</h1>"
                f"<p>team_id: {html.escape(team_id)}</p>"
                f"<p>alert_channel_id: {html.escape(alert_channel_id) or '(not set)'}</p>"
                "<p>You can close this page.</p>"
            ),
        )
    
    except Exception as e:
        log_error(context, action="oauth_callback_failed", error=e)
        return _html(500, "<h1>Internal Server Error</h1>")