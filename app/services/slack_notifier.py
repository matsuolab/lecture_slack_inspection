"""Slack通知"""
import re
import logging
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from .violation_detector import DetectionResult

logger = logging.getLogger(__name__)

WARNING_MESSAGE = """:warning: *ガイドライン違反のお知らせ*

<@{user_id}> さん、この投稿がコミュニティガイドラインに違反している可能性があります。

*該当条文:* {article_id}

恐れ入りますが、該当の投稿を削除していただけますようお願いいたします。
ご不明な点がございましたら、運営までお問い合わせください。
"""


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
):
    link = f"https://slack.com/archives/{channel_id}/p{message_ts.replace('.', '')}"

    # 確信度で重要度を決定
    if result.confidence >= 0.9:
        severity, emoji = "高", ":red_circle:"
    elif result.confidence >= 0.7:
        severity, emoji = "中", ":large_orange_circle:"
    else:
        severity, emoji = "低", ":large_yellow_circle:"

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
        {"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "投稿を確認"}, "url": link}
        ]}
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


def parse_post_link(post_link: str) -> tuple:
    """投稿リンクからchannel_idとmessage_tsを抽出"""
    if not post_link:
        return None, None

    match = re.search(r'/archives/([A-Z0-9]+)/p(\d+)', post_link)
    if match:
        channel_id = match.group(1)
        ts_raw = match.group(2)
        if len(ts_raw) > 6:
            message_ts = f"{ts_raw[:-6]}.{ts_raw[-6:]}"
        else:
            message_ts = ts_raw
        return channel_id, message_ts
    return None, None


def send_warning_reply(client: WebClient, log: dict) -> bool:
    """投稿にスレッド返信で警告を送信"""
    user_id = log.get("user_id", "").split("|")[0].strip()

    if not user_id.startswith("U"):
        logger.warning(f"Invalid user ID format: {log.get('user_id')}")
        return False

    channel_id, message_ts = parse_post_link(log.get("post_link"))
    if not channel_id or not message_ts:
        logger.error(f"Cannot parse post_link: {log.get('post_link')}")
        return False

    message = WARNING_MESSAGE.format(
        user_id=user_id,
        article_id=log.get("article_id") or "未特定"
    )

    try:
        client.chat_postMessage(
            channel=channel_id,
            thread_ts=message_ts,
            text=message
        )
        logger.info(f"Warning reply sent to {user_id}")
        return True
    except SlackApiError as e:
        logger.error(f"Failed to send warning reply: {e}")
        return False
