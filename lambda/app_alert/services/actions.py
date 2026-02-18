import json
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING
import logging
from slack_sdk import WebClient as SlackWebClient

@dataclass(frozen=True)
class ActionContext:
    action_id: str
    value: dict[str, Any]
    admin_channel: str | None
    admin_message_ts: str | None

def parse_action_context(payload: dict) -> ActionContext | None:
    
    # 新しいコード: typeが存在する場合のみチェックし、ない場合はスルーする
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
    return ActionContext(
        action_id=action_id,
        value=value,
        admin_channel=container.get("channel_id"),
        admin_message_ts=container.get("message_ts"),
    )

def handle_approve(slack: SlackWebClient, ctx: ActionContext, reply_text: str) -> None:
    origin_channel = ctx.value.get("origin_channel")
    origin_ts = ctx.value.get("origin_ts")
    if not origin_channel or not origin_ts:
        return

    try:
        if hasattr(slack, "post_message"):
            slack.post_message(channel=origin_channel, thread_ts=origin_ts, text=reply_text)
        else:
            slack.chat_postMessage(channel=origin_channel, thread_ts=origin_ts, text=reply_text)
    except Exception:
        pass

    if ctx.admin_channel and ctx.admin_message_ts:
        done_blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "✅ 対応しました（スレッドに削除勧告を送信済み）"}}]
        try:
            if hasattr(slack, "update_message"):
                slack.update_message(channel=ctx.admin_channel, ts=ctx.admin_message_ts, text="対応済み", blocks=done_blocks)
            else:
                slack.chat_update(channel=ctx.admin_channel, ts=ctx.admin_message_ts, text="対応済み", blocks=done_blocks)
        except Exception:
            pass


if TYPE_CHECKING:
    from slack_sdk import WebClient
    from common.notion_client import NotionClient

logger = logging.getLogger()

def handle_approve_violation(
    ctx: ActionContext, 
    slack: "WebClient", 
    notion: "NotionClient", 
    reply_text: str
) -> bool:
    origin_channel = ctx.value.get("origin_channel")
    origin_ts = ctx.value.get("origin_ts")
    notion_page_id = ctx.value.get("notion_page_id")

    if not origin_channel or not origin_ts:
        logger.error("Missing origin info for approve action")
        return False

    try:
        slack.chat_postMessage(
            channel=origin_channel,
            thread_ts=origin_ts,
            text=reply_text
        )
        logger.info(f"Posted warning to {origin_channel}/{origin_ts}")

        if notion_page_id:
            notion.update_status(notion_page_id, "Approved")
            logger.info(f"Updated Notion {notion_page_id} to Approved")

        if ctx.admin_channel and ctx.admin_message_ts:
            blocks = [
                {"type": "section", "text": {"type": "mrkdwn", "text": "✅ *対応完了* （警告送信済み）"}}
            ]
            slack.chat_update(
                channel=ctx.admin_channel,
                ts=ctx.admin_message_ts,
                text="Approved",
                blocks=blocks
            )
        return True

    except Exception as e:
        logger.error(f"Error executing approve_violation: {e}")
        return False

def handle_dismiss_violation(
    ctx: ActionContext, 
    slack: "WebClient", 
    notion: "NotionClient"
) -> bool:
    notion_page_id = ctx.value.get("notion_page_id")

    try:
        if notion_page_id:
            notion.update_status(notion_page_id, "Dismissed")
            logger.info(f"Updated Notion {notion_page_id} to Dismissed")
        else:
            logger.warning("Missing notion_page_id for dismiss action")

        if ctx.admin_channel and ctx.admin_message_ts:
            blocks = [
                {"type": "section", "text": {"type": "mrkdwn", "text": "🚫 *Dismissed* （対応不要）"}}
            ]
            slack.chat_update(
                channel=ctx.admin_channel,
                ts=ctx.admin_message_ts,
                text="Dismissed",
                blocks=blocks
            )
        return True

    except Exception as e:
        logger.error(f"Error executing dismiss_violation: {e}")
        return False
