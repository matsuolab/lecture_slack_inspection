from typing import Any, Callable

from common.observability import log_error, log_info

from .alert_service import (
    post_closed_by_edit_notice,
    post_edit_notice_to_thread,
    send_new_violation_alert,
)
from .models import InspectEvent, ModerationResult, SlackIdentity, severity_rank
from .violation_transition import decide_transition


def _extract_rich_text(page: dict, prop_name: str) -> str:
    props = page.get("properties") or {}
    rich_text = (props.get(prop_name) or {}).get("rich_text") or []
    if not rich_text:
        return ""

    parts: list[str] = []
    for item in rich_text:
        if not isinstance(item, dict):
            continue
        plain_text = item.get("plain_text")
        if plain_text:
            parts.append(str(plain_text))
            continue
        text_obj = item.get("text") or {}
        content = text_obj.get("content")
        if content:
            parts.append(str(content))
    return "".join(parts).strip()


def _extract_url(page: dict, prop_name: str) -> str | None:
    props = page.get("properties") or {}
    return (props.get(prop_name) or {}).get("url")


def _resolve_slack_identity(
    *,
    context: Any,
    slack_client: Any,
    raw_user_id: str,
    raw_channel_id: str,
    raw_team_id: str,
) -> SlackIdentity:
    profile_name = f"@{raw_user_id}" if raw_user_id else "（不明）"
    channel_name = raw_channel_id or "（不明）"
    workspace_name = raw_team_id or "（不明）"

    try:
        if raw_user_id:
            user_res = slack_client.users_info(user=raw_user_id)
            profile = user_res["user"]["profile"]
            profile_name = "@" + (
                profile.get("display_name") or profile.get("real_name") or raw_user_id
            )
        if raw_channel_id:
            channel_res = slack_client.conversations_info(channel=raw_channel_id)
            channel_name = channel_res["channel"]["name"]
        if raw_team_id:
            team_res = slack_client.team_info(team=raw_team_id)
            workspace_name = team_res["team"]["name"]
    except Exception as e:
        log_error(context, action="fetch_slack_names", error=e)

    return SlackIdentity(
        profile_name=profile_name,
        channel_name=channel_name,
        workspace_name=workspace_name,
    )


def _create_violation_record_and_alert(
    *,
    context: Any,
    cfg: Any,
    inspect_event: InspectEvent,
    slack_client: Any,
    notion: Any,
    result: ModerationResult,
) -> dict:
    identity = _resolve_slack_identity(
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

    article_display_name = result.article_id
    article_page_id = notion.find_article_page_id(result.article_id) if result.article_id else None
    if article_page_id:
        notion.set_article_relation(notion_page_id, article_page_id)
        article_display_name = notion.get_article_name(article_page_id) or article_display_name

    send_new_violation_alert(
        slack_client=slack_client,
        notion=notion,
        cfg=cfg,
        context=context,
        raw_user_id=inspect_event.user_id,
        raw_channel_id=inspect_event.channel_id,
        message_ts=inspect_event.message_ts,
        post_text=inspect_event.text,
        post_link=post_link,
        notion_page_id=notion_page_id,
        article_display_name=article_display_name,
        result=result,
        profile_name=identity.profile_name,
    )
    log_info(context, action="alert_sent", result="success", page_id=notion_page_id)

    return {"statusCode": 200, "body": "ok"}


def handle_new_message(
    *,
    context: Any,
    cfg: Any,
    inspect_event: InspectEvent,
    slack_client: Any,
    notion: Any,
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
    notion: Any,
    moderate_text: Callable[[str], ModerationResult],
) -> dict:
    result = moderate_text(inspect_event.text)
    existing_page = notion.find_violation_by_message_ts(inspect_event.message_ts)
    decision = decide_transition(existing_page, result)

    if decision.action == "no_action":
        log_info(context, action="edit_judge", result="no_action", status=decision.status)
        return {"statusCode": 200, "body": "ok"}

    if decision.action == "create_new_violation":
        log_info(context, action="edit_judge", result="create_new_violation")
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

    if not existing_page:
        log_info(context, action="edit_judge", result="missing_existing_page")
        return {"statusCode": 200, "body": "ok"}

    admin_channel_id = _extract_rich_text(existing_page, "通知チャンネルID")
    admin_message_ts = _extract_rich_text(existing_page, "通知メッセージTS")
    post_link = _extract_url(existing_page, "投稿リンク")
    page_id = existing_page.get("id")

    if decision.action == "append_edit_notice":
        log_info(context, action="edit_judge", result="append_edit_notice", status=decision.status)
        if admin_channel_id and admin_message_ts:
            post_edit_notice_to_thread(
                slack_client=slack_client,
                admin_channel_id=admin_channel_id,
                admin_message_ts=admin_message_ts,
                post_link=post_link,
                previous_text=inspect_event.previous_text,
                current_text=inspect_event.text,
            )
        return {"statusCode": 200, "body": "ok"}

    if decision.action == "close_violation_as_edited":
        log_info(context, action="edit_judge", result="close_violation_as_edited", status=decision.status)
        if page_id:
            identity = _resolve_slack_identity(
                context=context,
                slack_client=slack_client,
                raw_user_id=inspect_event.user_id,
                raw_channel_id=inspect_event.channel_id,
                raw_team_id=inspect_event.team_id,
            )
            notion.mark_closed_by_edit(page_id, responder_name=identity.profile_name)

        if admin_channel_id and admin_message_ts:
            post_closed_by_edit_notice(
                slack_client=slack_client,
                admin_channel_id=admin_channel_id,
                admin_message_ts=admin_message_ts,
                post_link=post_link,
                previous_text=inspect_event.previous_text,
                current_text=inspect_event.text,
            )
        return {"statusCode": 200, "body": "ok"}

    return {"statusCode": 200, "body": "ok"}