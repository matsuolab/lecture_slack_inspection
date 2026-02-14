"""警告送信"""
import logging
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

logger = logging.getLogger(__name__)

WARNING_MESSAGE = """:warning: *ガイドライン違反のお知らせ*

<@{user_id}> さん、この投稿がコミュニティガイドラインに違反している可能性があります。

*該当条文:* {article_id}

恐れ入りますが、該当の投稿を削除していただけますようお願いいたします。
ご不明な点がございましたら、運営までお問い合わせください。
"""


def send_warning_reply(
    client: WebClient, origin_channel: str, origin_ts: str,
    user_id: str, article_id: str = None,
) -> bool:
    """元投稿にスレッド返信で警告を送る"""
    if not origin_channel or not origin_ts:
        logger.error("Missing origin_channel or origin_ts")
        return False

    if not user_id or not user_id.startswith("U"):
        logger.warning(f"Invalid user ID format: {user_id}")
        return False

    message = WARNING_MESSAGE.format(
        user_id=user_id,
        article_id=article_id or "未特定",
    )

    try:
        client.chat_postMessage(
            channel=origin_channel,
            thread_ts=origin_ts,
            text=message,
        )
        logger.info(f"Warning reply sent to {user_id}")
        return True
    except SlackApiError as e:
        logger.error(f"Failed to send warning reply: {e}")
        return False
