import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from slack_sdk import WebClient
    from common.notion_client import NotionClient

from common.template_manager import resolve_template, render_template

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ActionContext:
    action_id: str
    value: dict[str, Any]
    admin_channel: str | None
    admin_message_ts: str | None
    admin_blocks: list[dict[str, Any]] | None
    selected_template_page_id: str | None


def _extract_template_selection(payload: dict) -> str | None:
    """block_actionsペイロードのstateからプルダウン選択値を取得（全block走査）"""
    state = payload.get("state", {}).get("values", {})
    for block_data in state.values():
        for action_data in block_data.values():
            selected = action_data.get("selected_option")
            if selected:
                return selected.get("value")
    return None


def parse_action_context(payload: dict) -> ActionContext | None:
    payload_type = payload.get("type")
    if payload_type is not None and payload_type != "block_actions":
        return None

    actions = payload.get("actions") or []
    if not actions:
        return None
    action = actions[0]
    action_id = action.get("action_id") or ""
    raw_value = action.get("value") or "{}"
    try:
        value = json.loads(raw_value) if isinstance(raw_value, str) else dict(raw_value)
    except Exception:
        value = {}

    container = payload.get("container") or {}

    msg = payload.get("message") or {}
    admin_blocks = msg.get("blocks") if isinstance(msg.get("blocks"), list) else None

    return ActionContext(
        action_id=action_id,
        value=value,
        admin_channel=container.get("channel_id"),
        admin_message_ts=container.get("message_ts"),
        admin_blocks=admin_blocks,
        selected_template_page_id=_extract_template_selection(payload),
    )


def _now_slack_datetime_token() -> str:
    now = datetime.now(timezone.utc)
    unix_sec = int(now.timestamp())
    fallback = now.strftime("%Y-%m-%d %H:%M")
    return f"<!date^{unix_sec}^{{date_short_pretty}} {{time}}|{fallback}>"


def _build_admin_updated_blocks(
    original_blocks: list[dict[str, Any]] | None,
    status_text: str,
) -> list[dict[str, Any]]:
    """元メッセージからボタン・プルダウンを除去し、ステータスを先頭に挿入"""
    status_block = {
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": status_text}],
    }

    new_blocks: list[dict[str, Any]] = [status_block]

    for b in (original_blocks or []):
        if b.get("type") == "actions":
            continue
        # プルダウン付きsectionを除去（warning_template / rewarn_template）
        if b.get("type") == "section" and b.get("accessory", {}).get("type") == "static_select":
            continue
        new_blocks.append(b)

    return new_blocks


def _update_admin_message(
    slack: "WebClient",
    context: ActionContext,
    responder_id: str | None,
    emoji: str,
    label: str,
) -> None:
    """管理chのメッセージをステータス表示に更新する共通処理"""
    if not context.admin_channel or not context.admin_message_ts:
        return
    responder_text = f"<@{responder_id}>" if responder_id else "（不明）"
    handled_at = _now_slack_datetime_token()
    status_text = f"{emoji} *{label}* by {responder_text} • {handled_at}"

    blocks = _build_admin_updated_blocks(context.admin_blocks, status_text)

    slack.chat_update(
        channel=context.admin_channel,
        ts=context.admin_message_ts,
        text=label,
        blocks=blocks,
    )


def handle_approve_violation(
    context: ActionContext,
    slack: "WebClient",
    notion: "NotionClient",
    responder_id: str | None = None,
    responder_name: str | None = None,
    notion_api_key: str = "",
    notion_template_db_id: str = "",
) -> bool:
    origin_channel = context.value.get("origin_channel")
    origin_ts = context.value.get("origin_ts")
    notion_page_id = context.value.get("notion_page_id")
    article_id = context.value.get("article_id")

    if not origin_channel or not origin_ts:
        logger.error("Missing origin info for approve action")
        return False

    template_body = resolve_template(
        "警告",
        api_key=notion_api_key,
        db_id=notion_template_db_id,
        template_page_id=context.selected_template_page_id or "",
    )
    warning_text = render_template(template_body, article=article_id or "")

    try:
        slack.chat_postMessage(
            channel=origin_channel,
            thread_ts=origin_ts,
            text=warning_text,
        )
        logger.info("Posted warning to %s/%s", origin_channel, origin_ts)

        if notion_page_id:
            notion.update_status(
                notion_page_id, "警告済み",
                warning_sent_at=datetime.now(timezone.utc),
                responder_id=responder_name or responder_id,
            )
            if context.selected_template_page_id:
                notion.set_template_relation(notion_page_id, context.selected_template_page_id)
            logger.info("Updated Notion %s to 警告済み", notion_page_id)

        _update_admin_message(slack, context, responder_id, "✅", "警告済み")
        return True

    except Exception as e:
        logger.error("Error executing approve_violation: %s", e)
        return False


def handle_dismiss_violation(
    context: ActionContext,
    slack: "WebClient",
    notion: "NotionClient",
    responder_id: str | None = None,
    responder_name: str | None = None,
) -> bool:
    notion_page_id = context.value.get("notion_page_id")

    try:
        if notion_page_id:
            notion.update_status(
                notion_page_id, "対応不要",
                responder_id=responder_name or responder_id,
            )
            logger.info("Updated Notion %s to 対応不要", notion_page_id)
        else:
            logger.warning("Missing notion_page_id for dismiss action")

        _update_admin_message(slack, context, responder_id, "🚫", "対応不要")
        return True

    except Exception as e:
        logger.error("Error executing dismiss_violation: %s", e)
        return False


def handle_rewarn_violation(
    context: ActionContext,
    slack: "WebClient",
    notion: "NotionClient",
    responder_id: str | None = None,
    responder_name: str | None = None,
    notion_api_key: str = "",
    notion_template_db_id: str = "",
) -> bool:
    """再警告ボタン押下時の処理: 違反投稿に再警告送信 + Notion更新"""
    origin_channel = context.value.get("origin_channel")
    origin_ts = context.value.get("origin_ts")
    notion_page_id = context.value.get("notion_page_id")
    article_id = context.value.get("article_id")

    if not origin_channel or not origin_ts:
        logger.error("Missing origin info for rewarn action")
        return False

    template_body = resolve_template(
        "再警告",
        api_key=notion_api_key,
        db_id=notion_template_db_id,
        template_page_id=context.selected_template_page_id or "",
    )
    rewarn_text = render_template(template_body, article=article_id or "")

    try:
        slack.chat_postMessage(
            channel=origin_channel,
            thread_ts=origin_ts,
            text=rewarn_text,
        )
        logger.info("Posted rewarn to %s/%s", origin_channel, origin_ts)

        if notion_page_id:
            notion.update_rewarn_info(
                notion_page_id,
                rewarn_sent_at=datetime.now(timezone.utc),
                template_page_id=context.selected_template_page_id or "",
                responder_name=responder_name or responder_id or "",
            )
            logger.info("Updated Notion %s to 再警告済み", notion_page_id)

        _update_admin_message(slack, context, responder_id, "🔔", "再警告済み")
        return True

    except Exception as e:
        logger.error("Error executing rewarn_violation: %s", e)
        return False


def handle_close_violation(
    context: ActionContext,
    slack: "WebClient",
    notion: "NotionClient",
    responder_id: str | None = None,
    responder_name: str | None = None,
) -> bool:
    """対応終了ボタン押下時の処理: Notionステータスを対応終了に更新"""
    notion_page_id = context.value.get("notion_page_id")

    try:
        if notion_page_id:
            notion.mark_closed(
                notion_page_id,
                responder_name=responder_name or responder_id or "",
            )
            logger.info("Updated Notion %s to 対応終了", notion_page_id)
        else:
            logger.warning("Missing notion_page_id for close action")

        _update_admin_message(slack, context, responder_id, "📋", "対応終了")
        return True

    except Exception as e:
        logger.error("Error executing close_violation: %s", e)
        return False
