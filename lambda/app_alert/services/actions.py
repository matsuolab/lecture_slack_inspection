import json
from dataclasses import dataclass
from typing import Any
from .slack_webapi import SlackWebClient

@dataclass(frozen=True)
class ActionContext:
    action_id: str
    value: dict[str, Any]
    admin_channel: str | None
    admin_message_ts: str | None

def parse_action_context(payload: dict) -> ActionContext | None:
    if payload.get("type") != "block_actions":
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

    slack.post_message(channel=origin_channel, thread_ts=origin_ts, text=reply_text)

    if ctx.admin_channel and ctx.admin_message_ts:
        done_blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "✅ 対応しました（スレッドに削除勧告を送信済み）"}}]
        try:
            slack.update_message(channel=ctx.admin_channel, ts=ctx.admin_message_ts, text="対応済み", blocks=done_blocks)
        except Exception:
            pass
