from __future__ import annotations

from typing import Any, Callable

from common.notion_client import NotionClient
from common.observability import log_error, log_info

from ..components.slack_identity import resolve_slack_identity
from ..components.slack_notifier import (
    send_edit_closed_reply,
    send_edit_still_violation_reply,
    send_new_violation_alert,
)
from .models import InspectEvent, ModerationResult, severity_rank
from .violation_transition import decide_edit_transition


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


def _extract_url_prop(page: dict[str, Any], prop_name: str) -> str | None:
    props = page.get("properties") or {}
    url = (props.get(prop_name) or {}).get("url")
    return url if isinstance(url, str) and url else None


def _extract_admin_thread_info(notion: NotionClient, existing_page: dict[str, Any]) -> tuple[str, str]:
    try:
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


def _resolve_article_display_name(
    notion: NotionClient,
    notion_page_id: str | None,
    article_id: str | None,
) -> str | None:
    if not article_id:
        return None

    article_display_name = article_id
    article_page_id = notion.find_article_page_id(article_id)
    if article_page_id:
        if notion_page_id:
            notion.set_article_relation(notion_page_id, article_page_id)
        resolved_name = notion.get_article_name(article_page_id)
        if isinstance(resolved_name, str) and resolved_name.strip():
            article_display_name = resolved_name.strip()

    return article_display_name


def _create_violation_record_and_alert(
    *,
    context: Any,
    cfg: Any,
    inspect_event: InspectEvent,
    slack_client: Any,
    notion: NotionClient,
    result: ModerationResult,
) -> dict:
    identity = resolve_slack_identity(
        context=context,
        slack_client=slack_client,
        raw_user_id=inspect_event.user_id,
        raw_channel_id=inspect_event.channel_id,
        raw_team_id=inspect_event.team_id,
    )

    if notion.check_duplicate_violation(inspect_event.message_ts):
        log_info(context, action="duplicate_skip", message_ts=inspect_event.message_ts)
        return {"statusCode": 200, "body": "duplicate"}

    permalink_resp = slack_client.chat_getPermalink(
        channel=inspect_event.channel_id,
        message_ts=inspect_event.message_ts,
    )
    post_link = permalink_resp.get("permalink")

    notion_page_id = notion.create_violation_log(
        post_content=inspect_event.text,
        user_id=identity.profile_name,
        channel=identity.channel_name,
        workspace=identity.workspace_name,
        result="Violation",
        method=getattr(result, "method", None) or "LLM",
        reason=result.rationale,
        severity=result.severity,
        categories=result.categories,
        post_link=post_link,
        article_id=result.article_id,
        confidence=result.confidence,
        message_ts=inspect_event.message_ts,
        team_id=inspect_event.team_id,
    )
    log_info(context, action="notion_page_created", page_id=notion_page_id)

    article_display_name = _resolve_article_display_name(
        notion=notion,
        notion_page_id=notion_page_id,
        article_id=result.article_id,
    )

    alert_resp = send_new_violation_alert(
        context=context,
        slack_client=slack_client,
        cfg=cfg,
        trace_id=context.trace_id,
        identity=identity,
        raw_user_id=inspect_event.user_id,
        raw_channel_id=inspect_event.channel_id,
        message_ts=inspect_event.message_ts,
        post_text=inspect_event.text,
        post_link=post_link,
        notion_page_id=notion_page_id,
        article_display_name=article_display_name,
        result=result,
    )

    if notion_page_id and alert_resp.get("ok"):
        notion.update_admin_notification(
            notion_page_id,
            admin_channel_id=cfg.alert_private_channel_id,
            admin_message_ts=alert_resp["ts"],
        )

    log_info(context, action="alert_sent", result="success", page_id=notion_page_id)
    return {"statusCode": 200, "body": "ok"}


def handle_new_message(
    *,
    context: Any,
    cfg: Any,
    inspect_event: InspectEvent,
    slack_client: Any,
    notion: NotionClient,
    moderate_text: Callable[[str], ModerationResult],
) -> dict:
    result = moderate_text(inspect_event.text)

    if not result.is_violation or severity_rank(result.severity) < severity_rank(cfg.min_severity_to_alert):
        log_info(context, action="judge", result="not_violation")
        return {"statusCode": 200, "body": "ok"}

    try:
        return _create_violation_record_and_alert(
            context=context,
            cfg=cfg,
            inspect_event=inspect_event,
            slack_client=slack_client,
            notion=notion,
            result=result,
        )
    except Exception as e:
        log_error(context, action="external_service_call", error=e)
        return {"statusCode": 200, "body": "error_handled"}


def handle_edited_message(
    *,
    context: Any,
    cfg: Any,
    inspect_event: InspectEvent,
    slack_client: Any,
    notion: NotionClient,
    moderate_text: Callable[[str], ModerationResult],
) -> dict:
    result = moderate_text(inspect_event.text)
    existing_page = notion.find_violation_by_message_ts(inspect_event.message_ts)

    decision = decide_edit_transition(
        existing_page=existing_page,
        result=result,
        min_severity_to_alert=cfg.min_severity_to_alert,
    )

    if decision.action == "no_action":
        log_info(
            context,
            action="edit_judge",
            result="no_action",
            status=decision.current_status,
        )
        return {"statusCode": 200, "body": "ok"}

    if decision.action == "create_new_violation":
        try:
            return _create_violation_record_and_alert(
                context=context,
                cfg=cfg,
                inspect_event=inspect_event,
                slack_client=slack_client,
                notion=notion,
                result=result,
            )
        except Exception as e:
            log_error(context, action="external_service_call", error=e)
            return {"statusCode": 200, "body": "error_handled"}

    assert existing_page is not None

    admin_channel_id, admin_message_ts = _extract_admin_thread_info(notion, existing_page)
    saved_post_link = _extract_url_prop(existing_page, "投稿リンク")

    if decision.action == "reply_still_violation":
        if admin_channel_id and admin_message_ts:
            send_edit_still_violation_reply(
                slack_client=slack_client,
                admin_channel_id=admin_channel_id,
                admin_message_ts=admin_message_ts,
                previous_text=inspect_event.previous_text or "",
                current_text=inspect_event.text,
                post_link=saved_post_link,
            )
        log_info(
            context,
            action="edit_judge",
            result="still_violation",
            status=decision.current_status,
        )
        return {"statusCode": 200, "body": "ok"}

    if decision.action == "close_by_edit":
        page_id = existing_page.get("id")
        if isinstance(page_id, str) and page_id:
            notion.mark_closed_by_edit(page_id)

        if admin_channel_id and admin_message_ts:
            send_edit_closed_reply(
                slack_client=slack_client,
                admin_channel_id=admin_channel_id,
                admin_message_ts=admin_message_ts,
                previous_text=inspect_event.previous_text or "",
                current_text=inspect_event.text,
                post_link=saved_post_link,
            )
        log_info(
            context,
            action="edit_judge",
            result="closed_by_edit",
            status=decision.current_status,
        )
        return {"statusCode": 200, "body": "ok"}

    return {"statusCode": 200, "body": "ok"}