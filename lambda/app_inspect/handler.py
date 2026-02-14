"""Lambda A: POST /slack/events"""
import os
import json
import logging
from typing import Optional

from slack_sdk.errors import SlackApiError

from common.observability import build_context, log_info, log_error, emit_metric, Timer
from common.handler_utils import get_secret, verify_slack_signature, get_slack_client, init_notion
from common.notion_client import check_duplicate_violation, create_violation_log
from app_inspect.services.violation_detector import ViolationDetector
from app_inspect.services.slack_notifier import get_user_name, notify_admin

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_detector: Optional[ViolationDetector] = None
_bot_user_id: Optional[str] = None

_COURSE_CHANNEL_MAP_RAW = os.environ.get("COURSE_CHANNEL_MAP", "gci:GCI,dl:DL,llm:LLM")


def _get_detector() -> ViolationDetector:
    global _detector
    if not _detector:
        api_key = get_secret("OPENAI_API_KEY_SECRET_ARN")
        data_dir = os.path.join(os.path.dirname(__file__), "services", "data")
        _detector = ViolationDetector(
            openai_api_key=api_key,
            articles_path=os.path.join(data_dir, "articles.json"),
            ng_patterns_path=os.path.join(data_dir, "ng_patterns.json"),
        )
    return _detector


def _is_monitored_channel(channel_id: str) -> bool:
    ids_str = os.environ.get("SLACK_MONITOR_CHANNEL_IDS", "")
    if not ids_str:
        return True  # 未設定なら全チャンネル監視
    return channel_id in ids_str.split(",")


def _detect_course(channel_name: str) -> Optional[str]:
    for pair in _COURSE_CHANNEL_MAP_RAW.split(","):
        parts = pair.strip().split(":")
        if len(parts) == 2 and parts[0] in channel_name:
            return parts[1]
    return None


def handler(event, context):
    ctx = build_context(event, context, service="app_inspect")
    t = Timer()

    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    body_str = event.get("body", "")

    log_info(ctx, action="received")
    emit_metric(ctx, "events_received", 1)

    # Slackリトライは即200で返す
    if headers.get("x-slack-retry-num"):
        log_info(ctx, action="retry_skip", retry_num=headers["x-slack-retry-num"])
        return {"statusCode": 200, "body": "ok"}

    try:
        body = json.loads(body_str)
    except (json.JSONDecodeError, TypeError):
        body = {}

    # URL Verification (Slack App設定時)
    if body.get("type") == "url_verification":
        log_info(ctx, action="url_verification")
        return {
            "statusCode": 200,
            "body": json.dumps({"challenge": body.get("challenge", "")}),
        }

    # 署名検証
    signing_secret = get_secret("SLACK_SIGNING_SECRET_ARN")
    if not verify_slack_signature(headers, body_str, signing_secret):
        log_info(ctx, action="verify_signature", result="fail")
        return {"statusCode": 401, "body": "Invalid signature"}

    if body.get("type") == "event_callback":
        slack_event = body.get("event", {})
        if slack_event.get("type") == "message":
            try:
                _handle_message_event(ctx, slack_event)
            except Exception as e:
                log_error(ctx, action="handle_message", error=e)
                emit_metric(ctx, "events_processed_failed", 1)

    emit_metric(ctx, "events_processed_success", 1)
    emit_metric(ctx, "processing_latency_ms", t.ms(), unit="Milliseconds")
    return {"statusCode": 200, "body": json.dumps({"ok": True})}


def _handle_message_event(ctx, slack_event: dict):
    if slack_event.get("bot_id") or slack_event.get("subtype"):
        log_info(ctx, action="skip", reason="bot_or_subtype")
        return

    text = slack_event.get("text", "")
    if not text.strip():
        log_info(ctx, action="skip", reason="empty_text")
        return

    channel_id = slack_event.get("channel", "")
    user_id = slack_event.get("user", "")
    message_ts = slack_event.get("ts", "")

    client = get_slack_client()

    global _bot_user_id
    if not _bot_user_id:
        try:
            _bot_user_id = client.auth_test()["user_id"]
        except SlackApiError:
            _bot_user_id = ""
    if user_id == _bot_user_id:
        log_info(ctx, action="skip", reason="own_message")
        return

    if not _is_monitored_channel(channel_id):
        log_info(ctx, action="skip", reason="not_monitored", channel=channel_id)
        return

    init_notion()
    if check_duplicate_violation(message_ts):
        log_info(ctx, action="skip", reason="duplicate", ts=message_ts)
        return

    try:
        channel_info = client.conversations_info(channel=channel_id)
        channel_name = channel_info["channel"]["name"]
    except SlackApiError:
        channel_name = channel_id

    course = _detect_course(channel_name)
    detector = _get_detector()
    result = detector.detect(text, course=course)

    log_info(ctx, action="judge", decision={
        "is_violation": result.is_violation,
        "confidence": result.confidence,
        "method": result.method,
    })

    if not result.is_violation:
        return

    _handle_violation(ctx, client, user_id, channel_id, channel_name, message_ts, text, result)


def _handle_violation(ctx, client, user_id, channel_id, channel_name, message_ts, text, result):
    post_link = f"https://slack.com/archives/{channel_id}/p{message_ts.replace('.', '')}"

    page_id = create_violation_log(
        post_id=message_ts,
        post_content=text,
        user_id=user_id,
        channel=channel_name,
        result=result.category or "違反",
        method=result.method,
        article_id=result.article_id,
        confidence=result.confidence,
        reason=result.reason,
        post_link=post_link,
    )
    log_info(ctx, action="notion_log_created", page_id=page_id)

    admin_channel_id = os.environ.get("SLACK_ADMIN_CHANNEL_ID", "")
    if not admin_channel_id:
        log_info(ctx, action="notify_admin", result="skip", reason="no_admin_channel")
        return

    user_name = get_user_name(client, user_id)

    try:
        notify_admin(
            client=client,
            user_id=user_id,
            user_name=user_name,
            channel_id=channel_id,
            channel_name=channel_name,
            message_ts=message_ts,
            text=text,
            result=result,
            admin_channel_id=admin_channel_id,
            trace_id=ctx.trace_id,
            page_id=page_id,
        )
        log_info(ctx, action="notify_admin", result="success")
        emit_metric(ctx, "violations_detected", 1)
    except Exception as e:
        log_error(ctx, action="notify_admin", error=e)
