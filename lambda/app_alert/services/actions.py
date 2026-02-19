import json
import os
import re
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, TYPE_CHECKING, Optional
from common.utils import parse_article_id

if TYPE_CHECKING:
    from slack_sdk import WebClient
    from common.notion_client import NotionClient

logger = logging.getLogger(__name__)

_ARTICLES_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "common", "data"
)

POLICY_BLOCK_ID = "policy_ref_block"
AID_REGULATION = "policy_regulation_select"
AID_ARTICLE = "policy_article_select"
AID_ITEM = "policy_item_select"

@dataclass(frozen=True)
class ActionContext:
    action_id: str
    value: dict[str, Any]
    admin_channel: str | None
    admin_message_ts: str | None
    selected_regulation: str | None = None
    selected_article: str | None = None   # "11" or "special"
    selected_item: str | None = None      # "4" or "0"


def _get_selected_value(payload: dict, block_id: str, action_id: str) -> str | None:
    state = (payload.get("state") or {}).get("values") or {}
    block = state.get(block_id) or {}
    action_data = block.get(action_id) or {}
    selected_option = action_data.get("selected_option") or {}
    return selected_option.get("value")


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

    selected_reg = _get_selected_value(payload, POLICY_BLOCK_ID, AID_REGULATION)
    selected_article = _get_selected_value(payload, POLICY_BLOCK_ID, AID_ARTICLE)
    selected_item = _get_selected_value(payload, POLICY_BLOCK_ID, AID_ITEM)

    container = payload.get("container") or {}
    return ActionContext(
        action_id=action_id,
        value=value,
        admin_channel=container.get("channel_id"),
        admin_message_ts=container.get("message_ts"),
        selected_regulation=selected_reg,
        selected_article=selected_article,
        selected_item=selected_item,
    )


def _load_articles_list() -> list[dict[str, Any]]:
    path = os.path.join(_ARTICLES_DIR, "articles.json")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("articles", [])

def _find_article_by_id(article_id: str | None) -> dict[str, Any] | None:
    if not article_id:
        return None
    for a in _load_articles_list():
        if a.get("id") == article_id:
            return a
    return None


def _find_article_by_selection(
    regulation: str | None,
    selected_article: str | None,
    selected_item: str | None,
) -> dict[str, Any] | None:
    if not regulation or not selected_article:
        return None
    if selected_article == "special":
        return None

    try:
        article_no = int(selected_article)
    except Exception:
        return None

    # blocks.py 仕様: "0" = 項なし
    item_no: int | None
    if not selected_item or selected_item == "0":
        item_no = None
    else:
        try:
            item_no = int(selected_item)
        except Exception:
            item_no = None

    for a in _load_articles_list():
        if a.get("regulation") != regulation:
            continue
        article_no, item_no = parse_article_id(a.get("id", ""))
        if article_no != article_no:
            continue
        if item_no is None and item_no is None:
            return a
        if item_no is not None and item_no == item_no:
            return a

    return None


def _format_ref(selected_regulation: str | None, selected_article: str | None, selected_item: str | None) -> str:
    regulation = selected_regulation or "規約不明"

    if not selected_article or selected_article == "special":
        return regulation

    ref = f"{regulation} 第{selected_article}条"
    if selected_item and selected_item not in ("0", "", "None"):
        ref += f" 第{selected_item}項"
    return ref

def _slack_date(ts: str) -> str:
    try:
        unix_ts = int(float(ts))
        return f"<!date^{unix_ts}^{{date_short_pretty}} {{time}}|{ts}>"
    except Exception:
        return ts


def build_warning_text(
    origin_user: str | None = None,
    selected_regulation: str | None = None,
    selected_article: str | None = None,
    selected_item: str | None = None,
) -> str:
    ref_text = _format_ref(selected_regulation, selected_article, selected_item)
    mention = f"<@{origin_user}> " if origin_user else ""

    return (
        f"{mention}松尾研AIEコミュニティ運営事務局です。\n"
        f"ご投稿頂いた内容は、{ref_text} に違反する可能性があるため、投稿の削除をお願いいたします "
        f"(2営業日以内にご対応頂けない場合は、誠に恐縮ですが運営にて削除をさせて頂きます)。\n"
        "本コミュニティでは、宣伝や告知に関するご投稿について、いくつかルールを設けさせていただいております。\n"
        "つきましては、こちらのコミュニティガイドの参加規約とコミュニケーションルールを今一度ご確認下さいますと幸いです。\n"
        "引き続き、松尾研AIEコミュニティをどうぞよろしくお願いいたします。"
    )



def handle_approve_violation(
    ctx: ActionContext,
    slack: "WebClient",
    notion: "NotionClient",
    reply_text: str,
    responder_id: str | None = None,
) -> bool:
    origin_channel = ctx.value.get("origin_channel")
    origin_ts = ctx.value.get("origin_ts")
    notion_page_id = ctx.value.get("notion_page_id")

    origin_user = ctx.value.get("origin_user")

    if not origin_channel or not origin_ts:
        logger.error("Missing origin info for approve action")
        return False

    # 1) 手動選択（プルダウン）を最優先
    article = _find_article_by_selection(
        ctx.selected_regulation,
        ctx.selected_article,
        ctx.selected_item,
    )

    # 2) フォールバック：ボタンvalueの article_id / detected_article_id
    if not article:
        article_id = ctx.value.get("article_id") or ctx.value.get("detected_article_id")
        article = _find_article_by_id(article_id)

    warning_text = build_warning_text(
        origin_user=origin_user,
        selected_regulation=ctx.selected_regulation,
        selected_article=ctx.selected_article,
        selected_item=ctx.selected_item,
    )

    try:
        slack.chat_postMessage(
            channel=origin_channel,
            thread_ts=origin_ts,
            text=warning_text,
        )
        logger.info(f"Posted warning to {origin_channel}/{origin_ts}")

        if notion_page_id:
            update_kwargs: dict[str, Any] = {}
            update_kwargs["warning_sent_at"] = datetime.now()
            if responder_id:
                update_kwargs["responder_id"] = responder_id
            # Notion への追記(保留)
            # update_kwargs["selected_regulation"] = ctx.selected_regulation
            # update_kwargs["selected_article"] = ctx.selected_article
            # update_kwargs["selected_item"] = ctx.selected_item

            notion.update_status(notion_page_id, "Approved", **update_kwargs)
            logger.info(f"Updated Notion {notion_page_id} to Approved")

        if ctx.admin_channel and ctx.admin_message_ts:
            responder_text = f" by <@{responder_id}>" if responder_id else ""

            origin_user = ctx.value.get("origin_user")
            origin_ts = ctx.value.get("origin_ts")
            origin_channel = ctx.value.get("origin_channel")

            ref = _format_ref(
                ctx.selected_regulation or None,
                ctx.selected_article or None,
                ctx.selected_item or None,
            )

            blocks = [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"✅ *対応完了*（警告送信済み）{responder_text}"}
                },
                {
                    "type": "context",
                    "elements": [{
                        "type": "mrkdwn",
                        "text": (
                            f"チャンネル: <#{origin_channel}>   "
                            f"投稿者: <@{origin_user}>   "
                            f"投稿日時: {_slack_date(origin_ts)}"
                        )
                    }]
                },
                {"type": "section", "text": {"type": "mrkdwn", "text": f"・条文: *{ref}*"}},
                {"type": "divider"},
                {"type": "context", "elements": [{"type": "mrkdwn", "text": "※本メッセージは対応完了状態に更新されました（元情報は保持）"}]},
            ]

            slack.chat_update(
                channel=ctx.admin_channel,
                ts=ctx.admin_message_ts,
                text="Approved",
                blocks=blocks,
            )


        return True

    except Exception as e:
        logger.error(f"Error executing approve_violation: {e}")
        return False


def handle_dismiss_violation(
    ctx: ActionContext,
    slack: "WebClient",
    notion: "NotionClient",
    responder_id: str | None = None,
) -> bool:
    notion_page_id = ctx.value.get("notion_page_id")

    try:
        if notion_page_id:
            update_kwargs: dict[str, Any] = {}
            if responder_id:
                update_kwargs["responder_id"] = responder_id
            notion.update_status(notion_page_id, "Dismissed", **update_kwargs)
            logger.info(f"Updated Notion {notion_page_id} to Dismissed")
        else:
            logger.warning("Missing notion_page_id for dismiss action")

        if ctx.admin_channel and ctx.admin_message_ts:
            responder_text = f" by <@{responder_id}>" if responder_id else ""
            blocks = [
                {"type": "section", "text": {"type": "mrkdwn",
                    "text": f"🚫 *Dismissed*（対応不要）{responder_text}"}}
            ]
            slack.chat_update(
                channel=ctx.admin_channel,
                ts=ctx.admin_message_ts,
                text="Dismissed",
                blocks=blocks,
            )
        return True

    except Exception as e:
        logger.error(f"Error executing dismiss_violation: {e}")
        return False
