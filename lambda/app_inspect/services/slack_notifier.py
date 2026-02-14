"""管理者通知"""
import json
import os
import logging
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from .violation_detector import DetectionResult

logger = logging.getLogger(__name__)

_SEVERITY_HIGH = float(os.environ.get("SEVERITY_HIGH_THRESHOLD", "0.9"))
_SEVERITY_MID = float(os.environ.get("SEVERITY_MID_THRESHOLD", "0.7"))


def get_user_name(client: WebClient, user_id: str) -> str:
    try:
        resp = client.users_info(user=user_id)
        user = resp.get("user", {})
        return user.get("real_name") or user.get("name") or user_id
    except SlackApiError:
        return user_id


def notify_admin(
    client: WebClient, user_id: str, user_name: str,
    channel_id: str, channel_name: str, message_ts: str,
    text: str, result: DetectionResult, admin_channel_id: str,
    trace_id: str = "", page_id: str = None,
):
    link = f"https://slack.com/archives/{channel_id}/p{message_ts.replace('.', '')}"

    if result.confidence >= _SEVERITY_HIGH:
        severity, emoji = "高", ":red_circle:"
    elif result.confidence >= _SEVERITY_MID:
        severity, emoji = "中", ":large_orange_circle:"
    else:
        severity, emoji = "低", ":large_yellow_circle:"

    button_value = json.dumps({
        "version": "v1",
        "trace_id": trace_id,
        "origin_channel": channel_id,
        "origin_ts": message_ts,
        "origin_user": user_id,
        "article_id": result.article_id or "",
        "reason": result.category or "",
        "page_id": page_id or "",
    }, ensure_ascii=False)

    action_elements = [
        {"type": "button", "text": {"type": "plain_text", "text": "投稿を確認"}, "url": link},
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "警告送信"},
            "style": "danger",
            "action_id": "approve_warning",
            "value": button_value,
        },
        {
            "type": "button",
            "text": {"type": "plain_text", "text": "対応不要"},
            "action_id": "dismiss_warning",
            "value": button_value,
        },
    ]

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": f"{emoji} ガイドライン違反検出（{severity}）"}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*投稿者:*\n<@{user_id}>"},
            {"type": "mrkdwn", "text": f"*チャンネル:*\n#{channel_name}"},
            {"type": "mrkdwn", "text": f"*検出方法:*\n{result.method}"},
            {"type": "mrkdwn", "text": f"*確信度:*\n{result.confidence:.0%}"},
        ]},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*投稿内容:*\n```{text[:500]}```"}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*該当条文:*\n{result.article_id or 'なし'}"},
            {"type": "mrkdwn", "text": f"*カテゴリ:*\n{result.category or 'なし'}"},
        ]},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*判定理由:*\n{result.reason}"}},
        {"type": "actions", "elements": action_elements},
    ]

    try:
        client.chat_postMessage(
            channel=admin_channel_id,
            blocks=blocks,
            text=f"ガイドライン違反検出: {result.category}",
        )
        logger.info("Admin notification sent")
    except SlackApiError as e:
        logger.error(f"Failed to send admin notification: {e}")
