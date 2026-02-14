"""Lambda B: POST /slack/interactions"""
import json
import logging
from datetime import datetime
from urllib.parse import parse_qs

from common.observability import build_context, log_info, log_error, emit_metric, Timer
from common.handler_utils import get_secret, verify_slack_signature, get_slack_client, init_notion
from common.notion_client import update_violation_status
from app_alert.services.slack_notifier import send_warning_reply

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event, context):
    ctx = build_context(event, context, service="app_alert")
    t = Timer()

    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    body_str = event.get("body", "")

    log_info(ctx, action="received")
    emit_metric(ctx, "events_received", 1)

    # 署名検証
    signing_secret = get_secret("SLACK_SIGNING_SECRET_ARN")
    if not verify_slack_signature(headers, body_str, signing_secret):
        log_info(ctx, action="verify_signature", result="fail")
        return {"statusCode": 401, "body": "Invalid signature"}

    # payload=<JSON> (x-www-form-urlencoded)
    try:
        parsed = parse_qs(body_str)
        payload = json.loads(parsed["payload"][0])
    except (KeyError, json.JSONDecodeError, IndexError):
        log_error(ctx, action="parse_payload",
                  error=ValueError("Failed to parse interaction payload"))
        return {"statusCode": 400, "body": "Bad request"}

    admin_user = payload.get("user", {}).get("name", "unknown")
    log_info(ctx, action="parsed", admin_user=admin_user,
             payload_type=payload.get("type"))

    if payload.get("type") == "block_actions":
        _handle_block_actions(ctx, payload)

    emit_metric(ctx, "events_processed_success", 1)
    emit_metric(ctx, "processing_latency_ms", t.ms(), unit="Milliseconds")
    return {"statusCode": 200, "body": json.dumps({"ok": True})}


def _handle_block_actions(ctx, payload: dict):
    actions = payload.get("actions", [])
    if not actions:
        return

    action = actions[0]
    action_id = action.get("action_id")
    raw_value = action.get("value", "")

    try:
        value = json.loads(raw_value)
    except (json.JSONDecodeError, TypeError):
        log_info(ctx, action="skip", reason="invalid_button_value")
        return

    origin_channel = value.get("origin_channel", "")
    origin_ts = value.get("origin_ts", "")
    origin_user = value.get("origin_user", "")
    article_id = value.get("article_id", "")
    page_id = value.get("page_id", "")
    trace_id = value.get("trace_id", "")

    if not origin_channel or not origin_ts:
        log_info(ctx, action="skip", reason="missing_origin_info",
                 action_id=action_id)
        return

    log_info(ctx, action="button_click", action_id=action_id,
             origin_trace_id=trace_id, page_id=page_id)

    client = get_slack_client()

    if action_id == "approve_warning":
        _approve_warning(ctx, client, origin_channel, origin_ts,
                         origin_user, article_id, page_id)
    elif action_id == "dismiss_warning":
        _dismiss_warning(ctx, page_id)
    else:
        log_info(ctx, action="unknown_action", action_id=action_id)


def _approve_warning(ctx, client, origin_channel, origin_ts,
                     origin_user, article_id, page_id):
    success = send_warning_reply(
        client=client,
        origin_channel=origin_channel,
        origin_ts=origin_ts,
        user_id=origin_user,
        article_id=article_id,
    )

    if success:
        log_info(ctx, action="warning_sent", origin_channel=origin_channel,
                 origin_ts=origin_ts)
        emit_metric(ctx, "warnings_sent", 1)

        # Notion更新は失敗しても警告自体は送信済み
        if page_id:
            try:
                init_notion()
                update_violation_status(
                    page_id, "警告送信済",
                    warning_sent_at=datetime.now(),
                )
                log_info(ctx, action="notion_status_updated",
                         page_id=page_id, status="警告送信済")
            except Exception as e:
                log_error(ctx, action="notion_status_update", error=e)
    else:
        log_error(ctx, action="warning_send",
                  error=RuntimeError("Failed to send warning reply"))


def _dismiss_warning(ctx, page_id: str):
    if page_id:
        try:
            init_notion()
            update_violation_status(page_id, "対応不要")
            log_info(ctx, action="dismissed", page_id=page_id)
        except Exception as e:
            log_error(ctx, action="dismiss_update", error=e)
    else:
        log_info(ctx, action="dismissed", reason="no_page_id")
