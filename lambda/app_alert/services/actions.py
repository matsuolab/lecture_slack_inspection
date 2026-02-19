import json
import logging
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

from slack_sdk.errors import SlackApiError

@dataclass(frozen=True)
class ActionContext:
    action_id: str
    value: dict[str, Any]
    admin_channel: str | None
    admin_message_ts: str | None

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
    return ActionContext(
        action_id=action_id,
        value=value,
        admin_channel=container.get("channel_id"),
        admin_message_ts=container.get("message_ts"),
    )

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

    message_already_deleted = False
    warning_sent = False

    #　ユーザーへの警告送信（この時点でslackの文章が削除されている可能性がある）
    try:　　
        slack.chat_postMessage(
            channel=origin_channel,
            thread_ts=origin_ts,
            text=reply_text
        )
        logger.info(f"Posted warning to {origin_channel}/{origin_ts}")
        warning_sent = True
    except SlackApiError as e:
        error_code = e.response.get("error")
        if error_code in ["message_not_found", "thread_not_found", "channel_not_found"]:
            # エラー内容の確認。message_not_foundなどの場合は処理を中断せず続行
            logger.warning(f"Origin message already deleted: {error_code}")
            message_already_deleted = True
        else:
            logger.error(f"Slack API error during postMessage: {e}")
            # Slack自体が死んでいる等、致命的な通信エラーの場合は後続を中断
            return False

    # Notionステータス更新（失敗しても全体は中断しない）
    notion_updated = False
    if notion_page_id:
        try:
            notion.update_status(notion_page_id, "Approved")
            logger.info(f"Updated Notion {notion_page_id} to Approved")
            notion_updated = True
        except Exception as e:
            logger.error(f"Failed to update Notion status: {e}")
            # エラーは記録するが、Slack UIを更新するために処理を続行させる

    # 管理者メッセージ更新
    if ctx.admin_channel and ctx.admin_message_ts:
        status_text = "✅ *対応完了*"
        if message_already_deleted:
            status_text += " （※対象の投稿は既に削除されていました）"
        elif warning_sent:
            status_text += " （警告送信済み）"
        
        # Notionの更新に失敗した場合は、運営にインシデントとして視覚的に伝える
        if notion_page_id and not notion_updated:
            status_text += "\n⚠️ *注: Notionのステータス更新に失敗しました*"

        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": status_text}}]
        try:
            slack.chat_update(
                channel=ctx.admin_channel,
                ts=ctx.admin_message_ts,
                text="Approved",
                blocks=blocks
            )
        except Exception as e:
            logger.error(f"Failed to update admin UI: {e}")
            return False

    return True

def handle_dismiss_violation(
    ctx: ActionContext, 
    slack: "WebClient", 
    notion: "NotionClient"
) -> bool:
    notion_page_id = ctx.value.get("notion_page_id")
    notion_updated = False

    # Notionステータス更新
    if notion_page_id:
        try:
            notion.update_status(notion_page_id, "Dismissed")
            logger.info(f"Updated Notion {notion_page_id} to Dismissed")
            notion_updated = True
        except Exception as e:
            logger.error(f"Failed to update Notion status for dismiss: {e}")
            # エラーは記録するが、ボタンを消去するために処理は続行

    # 管理者メッセージ更新
    if ctx.admin_channel and ctx.admin_message_ts:
        status_text = "🚫 *Dismissed* （対応不要）"
        if notion_page_id and not notion_updated:
            status_text += "\n⚠️ *注: Notionのステータス更新に失敗しました*"

        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": status_text}}]
        try:
            slack.chat_update(
                channel=ctx.admin_channel,
                ts=ctx.admin_message_ts,
                text="Dismissed",
                blocks=blocks
            )
        except Exception as e:
            logger.error(f"Failed to update admin UI: {e}")
            return False

    return True
