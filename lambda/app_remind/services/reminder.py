"""削除リマインドサービス: Notionポーリングで初回警告送信 + 48h後リマインド送信"""

import logging
from datetime import datetime, timezone
from typing import Optional

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from common.notion_client import NotionClient

logger = logging.getLogger(__name__)

WARNING_MESSAGE = (
    ":warning: *ガイドライン違反の通知*\n\n"
    "この投稿はコミュニティガイドラインに抵触する可能性があるため、"
    "投稿の削除または修正をお願いします。\n"
    "ご不明点がありましたら管理者までお問い合わせください。"
)

REMINDER_MESSAGE = (
    ":bell: *削除リマインド*\n\n"
    "この投稿はコミュニティガイドラインに抵触する可能性があるため、"
    "削除のお願いをしておりました。\n\n"
    "まだ投稿が残っているようですので、ご確認・削除をお願いいたします。\n"
    "ご不明点がありましたら管理者までお問い合わせください。"
)

TITLE_TRUNCATE_LEN = 50


def check_message_exists(slack: WebClient, channel_id: str, message_ts: str) -> bool:
    """元投稿がまだ存在するか確認"""
    try:
        resp = slack.conversations_history(
            channel=channel_id,
            oldest=message_ts,
            latest=message_ts,
            inclusive=True,
            limit=1,
        )
        messages = resp.get("messages", [])
        return len(messages) > 0 and messages[0].get("ts") == message_ts
    except SlackApiError as e:
        logger.error("Message existence check failed (%s/%s): %s", channel_id, message_ts, e)
        return False


def send_warning(slack: WebClient, channel_id: str, message_ts: str) -> bool:
    """スレッド返信で初回警告を送信"""
    try:
        slack.chat_postMessage(
            channel=channel_id,
            thread_ts=message_ts,
            text=WARNING_MESSAGE,
        )
        return True
    except SlackApiError as e:
        logger.error("Failed to send warning (%s/%s): %s", channel_id, message_ts, e)
        return False


def send_reminder(slack: WebClient, channel_id: str, message_ts: str) -> bool:
    """スレッド返信でリマインドを送信"""
    try:
        slack.chat_postMessage(
            channel=channel_id,
            thread_ts=message_ts,
            text=REMINDER_MESSAGE,
        )
        return True
    except SlackApiError as e:
        logger.error("Failed to send reminder (%s/%s): %s", channel_id, message_ts, e)
        return False


def _resolve_slack_client(
    workspace: Optional[str],
    slack_clients: dict[str, WebClient],
    default_client: WebClient,
) -> WebClient:
    """ワークスペース名からSlackクライアントを解決する"""
    if workspace and workspace in slack_clients:
        return slack_clients[workspace]
    return default_client


def process_reminders(
    slack: WebClient,
    notion: NotionClient,
    hours_threshold: int = 48,
    dry_run: bool = False,
    slack_clients: Optional[dict[str, WebClient]] = None,
) -> dict[str, int]:
    """Notionポーリングによる警告・リマインド処理のメインロジック

    Approved かつ リマインド未送信のレコードを取得し、以下を処理:
    - 警告送信日時が空 → 初回警告を送信 + 警告送信日時を記録
    - 警告送信日時あり + hours_threshold経過 → リマインドを送信

    Args:
        slack: デフォルトのSlack WebClient
        notion: NotionClient インスタンス
        hours_threshold: 警告後何時間でリマインドするか
        dry_run: Trueの場合、実際の送信・更新をしない
        slack_clients: ワークスペース名 -> WebClient のマッピング（マルチワークスペース対応）
    """
    if slack_clients is None:
        slack_clients = {}

    stats: dict[str, int] = {
        "queried": 0,
        "warned": 0,
        "skipped_not_elapsed": 0,
        "skipped_no_link": 0,
        "already_deleted": 0,
        "reminded": 0,
        "errors": 0,
    }

    pages = notion.query_approved_unreminded()
    stats["queried"] = len(pages)
    logger.info("Found %d approved & unreminded records", len(pages))

    for page in pages:
        fields = notion.extract_reminder_fields(page)
        page_id: str = fields["page_id"]
        title: str = fields["title"][:TITLE_TRUNCATE_LEN]

        parsed = notion.parse_slack_link(fields["post_link"])
        if not parsed:
            logger.warning("[SKIP] No valid post_link: %s", title)
            stats["skipped_no_link"] += 1
            continue

        channel_id, message_ts, workspace = parsed
        client = _resolve_slack_client(workspace, slack_clients, slack)

        if not check_message_exists(client, channel_id, message_ts):
            logger.info("[DELETED] Message already deleted: %s", title)
            stats["already_deleted"] += 1
            if not dry_run:
                notion.mark_reminded(page_id)
            continue

        # 警告送信日時が空 → 初回警告送信（Notion手動Approve等）
        if fields["warning_sent_at"] is None:
            if dry_run:
                logger.info("[DRY RUN] Would send warning: %s -> %s/%s (ws=%s)", title, channel_id, message_ts, workspace)
            else:
                if send_warning(client, channel_id, message_ts):
                    logger.info("[WARNED] Warning sent: %s (ws=%s)", title, workspace)
                    notion.update_status(page_id, "Approved", warning_sent_at=datetime.now(timezone.utc))
                else:
                    stats["errors"] += 1
                    continue
            stats["warned"] += 1
            continue

        # 警告送信日時あり → 閾値経過チェック → リマインド送信
        if not notion.is_past_threshold(fields["warning_sent_at"], hours_threshold):
            logger.info("[SKIP] Not yet %dh: %s", hours_threshold, title)
            stats["skipped_not_elapsed"] += 1
            continue

        if dry_run:
            logger.info("[DRY RUN] Would send reminder: %s -> %s/%s (ws=%s)", title, channel_id, message_ts, workspace)
            stats["reminded"] += 1
        else:
            if send_reminder(client, channel_id, message_ts):
                logger.info("[SENT] Reminder sent: %s (ws=%s)", title, workspace)
                stats["reminded"] += 1
                if not notion.mark_reminded(page_id):
                    logger.error("Failed to mark reminded: %s", page_id)
                    stats["errors"] += 1
            else:
                stats["errors"] += 1

    return stats
