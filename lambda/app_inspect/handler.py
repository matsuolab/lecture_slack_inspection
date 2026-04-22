import json
import base64
from typing import Any

from slack_sdk import WebClient
from slack_sdk.signature import SignatureVerifier
from openai import OpenAI

from common.observability import build_context, log_info, log_error, emit_metric, Timer
from common.notion_client import NotionClient
from common.template_manager import get_template_options
from common.health import write_health

from .services.config import load_config, load_signing_secret
from .services.moderation import run_moderation
from .components.slack_builder import encode_alert_button_value
from .components.slack_ui import build_violation_alert_blocks
from .services.models import severity_rank, ModerationResult

SERVICE = "app_inspect"
_ACTIVE_VIOLATION_STATUSES = {"警告済み", "期限超過", "再警告済み"}


def _decode_body(event: dict) -> str:
    body = event.get("body", "") or ""
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")
    return body


def _as_non_empty_str(value: Any) -> str | None:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return None


def _extract_rich_text_prop(page: dict[str, Any], prop_name: str) -> str:
    props = page.get("properties") or {}
    rich_text = (props.get(prop_name) or {}).get("rich_text") or []
    if not rich_text:
        return ""

    first = rich_text[0]
    if not isinstance(first, dict):
        return ""

    plain_text = first.get("plain_text")
    if isinstance(plain_text, str):
        return plain_text

    text_obj = first.get("text") or {}
    content = text_obj.get("content")
    if isinstance(content, str):
        return content

    return ""


def _extract_select_name(page: dict[str, Any], prop_name: str) -> str:
    props = page.get("properties") or {}
    select_obj = (props.get(prop_name) or {}).get("select") or {}
    name = select_obj.get("name")
    return name if isinstance(name, str) else ""


def _extract_url_prop(page: dict[str, Any], prop_name: str) -> str | None:
    props = page.get("properties") or {}
    url = (props.get(prop_name) or {}).get("url")
    return url if isinstance(url, str) and url else None

def _extract_admin_thread_info(notion: Any, existing_page: dict[str, Any]) -> tuple[str, str]:
    """
    既存違反レコードから、管理通知先の channel / thread_ts を取り出す。
    1) notion.extract_reminder_fields(existing_page) が使えるならそれを優先
    2) なければ properties を直接読む
    """
    try:
        if hasattr(notion, "extract_reminder_fields"):
            fields = notion.extract_reminder_fields(existing_page)
            if isinstance(fields, dict):
                admin_channel_id = fields.get("admin_channel_id")
                admin_message_ts = fields.get("admin_message_ts")
                if isinstance(admin_channel_id, str) and isinstance(admin_message_ts, str):
                    if admin_channel_id and admin_message_ts:
                        return admin_channel_id, admin_message_ts
    except Exception:
        pass

    admin_channel_id = _extract_rich_text_prop(existing_page, "通知チャンネルID")
    admin_message_ts = _extract_rich_text_prop(existing_page, "通知メッセージTS")
    return admin_channel_id, admin_message_ts

def _is_active_violation_status(status: str) -> bool:
    return status in _ACTIVE_VIOLATION_STATUSES


def _truncate_text(text: str, limit: int = 180) -> str:
    normalized = " ".join((text or "").split())
    if not normalized:
        return "（空）"
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1] + "…"


def lambda_handler(event: dict, context: Any) -> dict:
    context = build_context(event, context, service=SERVICE)
    total_timer = Timer()
    log_info(context, action="request_received")

    try:
        raw_headers = event.get("headers") or {}
        lower_headers = {k.lower(): v for k, v in raw_headers.items()}
        if lower_headers.get("x-slack-retry-num"):
            log_info(context, action="retry_skip", retry_num=lower_headers["x-slack-retry-num"])
            return {"statusCode": 200, "body": "ok"}

        body = _decode_body(event)
        headers = event.get("headers") or {}
        verifier = SignatureVerifier(load_signing_secret())
        if not verifier.is_valid_request(body, headers):
            log_info(context, action="verify_signature", result="fail", detail="invalid signature")
            return {"statusCode": 401, "body": "invalid signature"}

        try:
            body_json = json.loads(body)
        except Exception as e:
            log_error(context, action="parse_json", error=e)
            return {"statusCode": 400, "body": "invalid json"}

        if body_json.get("type") == "url_verification":
            log_info(context, action="url_verification", result="success")
            return {
                "statusCode": 200,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"challenge": body_json.get("challenge", "")}),
            }

        team_id = body_json.get("team_id") or context.slack_team_id
        if not team_id:
            log_info(context, action="missing_team_id", result="fail")
            return {"statusCode": 400, "body": "missing team_id"}

        cfg = load_config(team_id)
        ev = body_json.get("event", {}) or {}

        if body_json.get("type") != "event_callback" or ev.get("type") != "message":
            return {"statusCode": 200, "body": "ignored"}

        if ev.get("bot_id"):
            return {"statusCode": 200, "body": "ignored"}

        subtype = ev.get("subtype")
        is_edit_event = False
        previous_text = ""
        raw_user_id = ""
        raw_channel_id = ""
        raw_team_id = body_json.get("team_id", "")
        message_ts = ""

        if not subtype:
            text = str(ev.get("text", "") or "").strip()
            if not text:
                return {"statusCode": 200, "body": "empty_text"}

            raw_user_id = str(ev.get("user", "") or "")
            raw_channel_id = str(ev.get("channel", "") or "")
            message_ts = str(ev.get("ts", "") or "")

        elif subtype == "message_changed":
            message = ev.get("message") or {}
            previous_message = ev.get("previous_message") or {}

            if message.get("bot_id") or previous_message.get("bot_id"):
                return {"statusCode": 200, "body": "ignored"}

            text = str(message.get("text", "") or "").strip()
            previous_text = str(previous_message.get("text", "") or "").strip()

            if not text:
                return {"statusCode": 200, "body": "empty_text"}

            if text == previous_text:
                return {"statusCode": 200, "body": "ignored"}

            is_edit_event = True
            raw_user_id = str(message.get("user") or previous_message.get("user") or "")
            raw_channel_id = str(ev.get("channel") or message.get("channel") or "")
            message_ts = str(message.get("ts") or previous_message.get("ts") or "")

        else:
            return {"statusCode": 200, "body": "ignored"}

        log_info(context, action="start_moderation", text_length=len(text))
        inference_timer = Timer()

        if cfg.use_mock_openai:
            is_mock_violation = "違反" in text
            result = ModerationResult(
                is_violation=is_mock_violation,
                severity="medium",
                categories=["mock_test"],
                rationale="[MOCK] 違反ワード検知",
                recommended_reply="[MOCK] 削除を推奨します",
                confidence=0.9,
                article_id="mock_article_123",
                method="Mock",
            )
        else:
            openai_client = OpenAI(api_key=cfg.openai_api_key)
            result = run_moderation(openai_client, cfg.openai_model, cfg.guidelines_text, text)

        emit_metric(context, "InferenceLatencyMs", inference_timer.ms(), unit="Milliseconds")

        if not is_edit_event:
            if not result.is_violation or severity_rank(result.severity) < severity_rank(cfg.min_severity_to_alert):
                log_info(context, action="judge", result="not_violation")
                return {"statusCode": 200, "body": "ok"}

        slack_client = WebClient(token=cfg.slack_bot_token)
        profile_name = f"@{raw_user_id}" if raw_user_id else "（不明）"
        channel_name = raw_channel_id or "（不明）"
        workspace_name = raw_team_id or "（不明）"
        post_link = None
        notion_page_id = None
        article_display_name = _as_non_empty_str(getattr(result, "article_id", None))

        try:
            notion = NotionClient(cfg.notion_api_key, cfg.notion_db_id, cfg.notion_articles_db_id)

            if is_edit_event:
                existing_page = notion.find_violation_by_message_ts(message_ts)

                if existing_page:
                    admin_channel_id, admin_message_ts = _extract_admin_thread_info(notion, existing_page)
                    saved_post_link = _extract_url_prop(existing_page, "投稿リンク")
                    current_status = _extract_select_name(existing_page, "対応ステータス")
                    link_line = f"\n投稿リンク: {saved_post_link}" if saved_post_link else ""

                    # 2. もともと違反判定で通知済み → 編集後も違反
                    if result.is_violation:
                        if severity_rank(result.severity) >= severity_rank(cfg.min_severity_to_alert):
                            if admin_channel_id and admin_message_ts:
                                slack_client.chat_postMessage(
                                    channel=admin_channel_id,
                                    thread_ts=admin_message_ts,
                                    text=(
                                        "投稿が編集されました。"
                                        f"\n編集前: {_truncate_text(previous_text)}"
                                        f"\n編集後: {_truncate_text(text)}"
                                        f"{link_line}"
                                    ),
                                )
                            log_info(context, action="edit_judge", result="still_violation")
                        else:
                            # 違反ではあるが通知閾値未満。close はしない
                            log_info(
                                context,
                                action="edit_judge",
                                result="still_violation_below_threshold",
                                severity=result.severity,
                                status=current_status,
                            )

                        emit_metric(context, "TotalLatencyMs", total_timer.ms(), unit="Milliseconds")
                        write_health(SERVICE, "正常", notion_api_key=cfg.notion_api_key)
                        return {"statusCode": 200, "body": "ok"}

                    # 3. もともと違反 → 編集で非違反なら、削除と同じ扱いで終了
                    if _is_active_violation_status(current_status):
                        page_id = existing_page.get("id")
                        if isinstance(page_id, str) and page_id:
                            if hasattr(notion, "mark_closed_by_edit"):
                                notion.mark_closed_by_edit(page_id)
                            else:
                                notion.mark_closed(page_id)

                        if admin_channel_id and admin_message_ts:
                            slack_client.chat_postMessage(
                                channel=admin_channel_id,
                                thread_ts=admin_message_ts,
                                text=(
                                    "投稿が編集され、違反ではなくなったため対応終了にしました。"
                                    f"\n編集前: {_truncate_text(previous_text)}"
                                    f"\n編集後: {_truncate_text(text)}"
                                    f"{link_line}"
                                ),
                            )

                        log_info(context, action="edit_judge", result="closed_by_edit", status=current_status)
                    else:
                        # すでに対応終了など active でないものは再closeしない
                        log_info(context, action="edit_judge", result="already_closed_or_inactive", status=current_status)

                    emit_metric(context, "TotalLatencyMs", total_timer.ms(), unit="Milliseconds")
                    write_health(SERVICE, "正常", notion_api_key=cfg.notion_api_key)
                    return {"statusCode": 200, "body": "ok"}

                # 1. もともと非違反 → 編集で違反なら新規違反として扱う
                if not result.is_violation or severity_rank(result.severity) < severity_rank(cfg.min_severity_to_alert):
                    log_info(context, action="edit_judge", result="not_violation")
                    emit_metric(context, "TotalLatencyMs", total_timer.ms(), unit="Milliseconds")
                    write_health(SERVICE, "正常", notion_api_key=cfg.notion_api_key)
                    return {"statusCode": 200, "body": "ok"}

            try:
                if raw_user_id:
                    u_res = slack_client.users_info(user=raw_user_id)
                    p = u_res["user"]["profile"]
                    profile_name = "@" + (p.get("display_name") or p.get("real_name") or raw_user_id)

                if raw_channel_id:
                    c_res = slack_client.conversations_info(channel=raw_channel_id)
                    channel_name = c_res["channel"]["name"]

                if raw_team_id:
                    t_res = slack_client.team_info(team=raw_team_id)
                    workspace_name = t_res["team"]["name"]
            except Exception as e:
                log_error(context, action="fetch_slack_names", error=e)

            if notion.check_duplicate_violation(message_ts):
                log_info(context, action="duplicate_skip", message_ts=message_ts)
                return {"statusCode": 200, "body": "duplicate"}

            permalink_resp = slack_client.chat_getPermalink(channel=raw_channel_id, message_ts=message_ts)
            post_link = permalink_resp.get("permalink")

            notion_page_id = notion.create_violation_log(
                post_content=text,
                user_id=profile_name,
                channel=channel_name,
                workspace=workspace_name,
                result="Violation",
                method=getattr(result, "method", None) or "LLM",
                reason=result.rationale,
                severity=result.severity,
                categories=result.categories,
                post_link=post_link,
                article_id=result.article_id,
                confidence=result.confidence,
                message_ts=message_ts,
                team_id=team_id,
            )
            log_info(context, action="notion_page_created", page_id=notion_page_id)

            article_page_id = notion.find_article_page_id(result.article_id) if result.article_id else None
            article_page_id = _as_non_empty_str(article_page_id)

            if article_page_id:
                safe_notion_page_id = _as_non_empty_str(notion_page_id)
                if safe_notion_page_id:
                    notion.set_article_relation(safe_notion_page_id, article_page_id)

                resolved_name = notion.get_article_name(article_page_id)
                safe_resolved_name = _as_non_empty_str(resolved_name)
                if safe_resolved_name:
                    article_display_name = safe_resolved_name

        except Exception as e:
            log_error(context, action="external_service_call", error=e)
            notion_page_id = None

        safe_notion_page_id = _as_non_empty_str(notion_page_id)
        safe_article_display_name = article_display_name or _as_non_empty_str(getattr(result, "article_id", None))

        button_value = encode_alert_button_value(
            notion_page_id=safe_notion_page_id,
            trace_id=context.trace_id,
            origin_channel=raw_channel_id,
            origin_ts=message_ts,
            reason=result.rationale,
            article_id=safe_article_display_name,
        )

        if cfg.use_mock_openai:
            detection_method = "Mock"
            confidence_for_ui = None
        else:
            raw_method = getattr(result, "method", None)
            detection_method = raw_method if isinstance(raw_method, str) and raw_method else "LLM"
            confidence_for_ui = result.confidence if detection_method == "LLM" else None

        template_options = None
        notion_template_db_id = _as_non_empty_str(getattr(cfg, "notion_template_db_id", None))
        if notion_template_db_id:
            try:
                raw_options = get_template_options(cfg.notion_api_key, notion_template_db_id)
                if raw_options:
                    template_options = [
                        (o["name"], o["page_id"])
                        for o in raw_options
                        if o.get("usage") == "警告"
                    ]
            except Exception as e:
                log_error(context, action="fetch_template_options", error=e)

        blocks = build_violation_alert_blocks(
            author_user_id=(raw_user_id or None),
            author_display=(profile_name or None),
            origin_channel_id=raw_channel_id,
            post_link=post_link,
            post_ts=message_ts,
            post_text=text,
            detection_method=detection_method,
            confidence=confidence_for_ui,
            rationale=result.rationale,
            guideline_article=safe_article_display_name,
            categories=result.categories,
            button_value=button_value,
            warning_template_options=template_options,
        )

        alert_resp = slack_client.chat_postMessage(
            channel=cfg.alert_private_channel_id,
            text="〖違反検知アラート〗",
            blocks=blocks,
        )

        if safe_notion_page_id and alert_resp.get("ok"):
            try:
                notion.update_admin_notification(
                    safe_notion_page_id,
                    admin_channel_id=cfg.alert_private_channel_id,
                    admin_message_ts=alert_resp["ts"],
                )
            except Exception as e:
                log_error(context, action="save_admin_notification", error=e)

        log_info(context, action="alert_sent", result="success", page_id=safe_notion_page_id)
        emit_metric(context, "TotalLatencyMs", total_timer.ms(), unit="Milliseconds")
        write_health(SERVICE, "正常", notion_api_key=cfg.notion_api_key)
        return {"statusCode": 200, "body": "ok"}

    except Exception as e:
        log_error(context, action="handler_process", error=e)
        emit_metric(context, "handler_error", 1)
        write_health(SERVICE, "エラー", str(e))
        return {"statusCode": 200, "body": "error_handled"}