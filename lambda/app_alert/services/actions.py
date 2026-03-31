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

# app_inspect/components/slack_ui.py に合わせた block_id
_WARNING_TEMPLATE_BLOCK_ID = "warning_template"


@dataclass(frozen=True)
class ActionContext:
    action_id: str
    value: dict[str, Any]
    admin_channel: str | None
    admin_message_ts: str | None
    # 運営メッセージの元 blocks（押下後も詳細を残すため）
    admin_blocks: list[dict[str, Any]] | None
    # プルダウンで選択されたテンプレートのpage_id
    selected_template_page_id: str | None


def _extract_template_selection(payload: dict) -> str | None:
    """block_actionsペイロードのstateからプルダウン選択値を取得"""
    state = payload.get("state", {}).get("values", {})
    template_block = state.get(_WARNING_TEMPLATE_BLOCK_ID, {})
    for action_data in template_block.values():
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

    # 元メッセージの blocks を取得
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
    fallback = now.strftime("%Y-%m-%d %H:%M")  # フォールバック（分まで）
    return f"<!date^{unix_sec}^{{date_short_pretty}} {{time}}|{fallback}>"


def _build_admin_updated_blocks(
    original_blocks: list[dict[str, Any]] | None,
    status_text: str,
) -> list[dict[str, Any]]:
    status_block = {
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": status_text}],
    }

    new_blocks: list[dict[str, Any]] = [status_block]

    for b in (original_blocks or []):
        # ボタン（approve/dismiss）を消す
        if b.get("type") == "actions":
            continue

        # プルダウン（warning_template）を消す
        if b.get("type") == "section" and b.get("block_id") == _WARNING_TEMPLATE_BLOCK_ID:
            continue

        new_blocks.append(b)

    return new_blocks


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
        logger.info(f"Posted warning to {origin_channel}/{origin_ts}")

        if notion_page_id:
            update_kwargs: dict[str, Any] = {}
            update_kwargs["warning_sent_at"] = datetime.now()  # 既存挙動のまま
            if responder_name:
                update_kwargs["responder_id"] = responder_name
            elif responder_id:
                update_kwargs["responder_id"] = responder_id # 万が一名前が取れなかった時の保険
            notion.update_status(notion_page_id, "警告済み", **update_kwargs)
            if context.selected_template_page_id:
                notion.set_template_relation(notion_page_id, context.selected_template_page_id)
            logger.info(f"Updated Notion {notion_page_id} to 警告済み")

        # 運営メッセージを「詳細は残して、ボタン/プルダウンだけ消す」形で更新 + 対応日時を表示
        if context.admin_channel and context.admin_message_ts:
            responder_text = f"<@{responder_id}>" if responder_id else "（不明）"
            handled_at = _now_slack_datetime_token()
            status_text = f"✅ *警告済み* by {responder_text} • {handled_at}"

            blocks = _build_admin_updated_blocks(context.admin_blocks, status_text)

            slack.chat_update(
                channel=context.admin_channel,
                ts=context.admin_message_ts,
                text="警告済み",
                blocks=blocks,
            )

        return True

    except Exception as e:
        logger.error(f"Error executing approve_violation: {e}")
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
            update_kwargs: dict[str, Any] = {}
            # NotionへはSlackのIDではなく、表示名（responder_name）を渡す
            if responder_name:
                update_kwargs["responder_id"] = responder_name
            elif responder_id:
                update_kwargs["responder_id"] = responder_id
            notion.update_status(notion_page_id, "対応不要", **update_kwargs)
            logger.info(f"Updated Notion {notion_page_id} to 対応不要")
        else:
            logger.warning("Missing notion_page_id for dismiss action")

        # 運営メッセージを「詳細は残して、ボタン/プルダウンだけ消す」形で更新 + 対応日時を表示
        if context.admin_channel and context.admin_message_ts:
            responder_text = f"<@{responder_id}>" if responder_id else "（不明）"
            handled_at = _now_slack_datetime_token()
            status_text = f"🚫 *対応不要* by {responder_text} • {handled_at}"

            blocks = _build_admin_updated_blocks(context.admin_blocks, status_text)

            slack.chat_update(
                channel=context.admin_channel,
                ts=context.admin_message_ts,
                text="対応不要",
                blocks=blocks,
            )

        return True

    except Exception as e:
        logger.error(f"Error executing dismiss_violation: {e}")
        return False
