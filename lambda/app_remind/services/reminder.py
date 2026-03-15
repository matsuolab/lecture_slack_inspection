"""削除リマインドサービス: Notionポーリングで初回警告送信 + 48h経過ステータス更新 + 削除リマインド送信"""

import logging
from datetime import datetime, timezone
from typing import Optional

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from common.notion_client import NotionClient
from common.template_manager import resolve_template, render_template, compose_message

logger = logging.getLogger(__name__)

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


def _send_thread_message(slack: WebClient, channel_id: str, message_ts: str, text: str, log_prefix: str) -> bool:
    """スレッド返信でメッセージを送信"""
    try:
        slack.chat_postMessage(
            channel=channel_id,
            thread_ts=message_ts,
            text=text,
        )
        return True
    except SlackApiError as e:
        logger.error("Failed to send %s (%s/%s): %s", log_prefix, channel_id, message_ts, e)
        return False


def send_warning(slack: WebClient, channel_id: str, message_ts: str, text: str) -> bool:
    return _send_thread_message(slack, channel_id, message_ts, text, "warning")


def send_reminder(slack: WebClient, channel_id: str, message_ts: str, text: str) -> bool:
    return _send_thread_message(slack, channel_id, message_ts, text, "reminder")


def _resolve_slack_client(
    workspace: Optional[str],
    slack_clients: dict[str, WebClient],
    default_client: WebClient,
) -> WebClient:
    """ワークスペース名からSlackクライアントを解決する"""
    if workspace and workspace in slack_clients:
        return slack_clients[workspace]
    return default_client


def _build_message(usage: str, fields: dict, notion_api_key: str = "", template_db_id: str = "") -> str:
    """用途に応じたメッセージを組み立てる

    優先順位:
    1. Relation指定（template_page_id）→ そのテンプレート
    2. テンプレートDB → 用途に一致するもの
    3. フォールバック
    """
    template_body = resolve_template(
        usage,
        api_key=notion_api_key,
        db_id=template_db_id,
        template_page_id=fields.get("template_page_id", ""),
    )
    rendered = render_template(
        template_body,
        article=fields.get("article_id") or "",
        post_link=fields.get("post_link") or "",
        poster=fields.get("poster") or "",
    )
    return compose_message(rendered, fields.get("additional_message", ""))


def process_reminders(
    slack: WebClient,
    notion: NotionClient,
    hours_threshold: int = 48,
    dry_run: bool = False,
    slack_clients: Optional[dict[str, WebClient]] = None,
    notion_api_key: str = "",
    template_db_id: str = "",
) -> dict[str, int]:
    """Notionポーリングによる警告・48h経過ステータス更新のメインロジック

    Approved のレコードを取得し、以下を処理:
    - 警告送信日時が空 → 違反投稿スレッドに初回警告を送信 + 警告送信日時を記録
    - 警告送信日時あり + hours_threshold経過 → ステータスを 48h_Over に更新

    Args:
        slack: デフォルトのSlack WebClient
        notion: NotionClient インスタンス
        hours_threshold: 警告後何時間で48h_Overにするか
        dry_run: Trueの場合、実際の送信・更新をしない
        slack_clients: ワークスペース名 -> WebClient のマッピング（マルチワークスペース対応）
        notion_api_key: テンプレートDB取得用（空ならフォールバック使用）
        template_db_id: テンプレートDBのID（空ならフォールバック使用）
    """
    if slack_clients is None:
        slack_clients = {}

    stats: dict[str, int] = {
        "queried": 0,
        "warned": 0,
        "skipped_not_elapsed": 0,
        "skipped_no_link": 0,
        "already_deleted": 0,
        "expired": 0,
        "errors": 0,
    }

    pages = notion.query_approved_unreminded()
    stats["queried"] = len(pages)
    logger.info("Found %d approved records", len(pages))

    for page in pages:
        fields = notion.extract_reminder_fields(page)
        page_id: str = fields["page_id"]
        title: str = fields["title"][:TITLE_TRUNCATE_LEN]

        # 対象条文 Relation から条文名を解決（article_id より優先）
        if fields.get("article_page_id"):
            article_name = notion.get_article_name(fields["article_page_id"])
            if article_name:
                fields["article_id"] = article_name

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

        # 警告送信日時が空 → 初回警告を違反投稿スレッドに送信
        if fields["warning_sent_at"] is None:
            # Lambda Bとの競合防止: 送信前にNotionを再取得して確認
            fresh_page = notion.get_page(page_id)
            if fresh_page:
                fresh_fields = notion.extract_reminder_fields(fresh_page)
                if fresh_fields["warning_sent_at"] is not None:
                    logger.info("[SKIP] Already warned by Lambda B: %s", title)
                    stats["skipped_not_elapsed"] += 1
                    continue

            message = _build_message("警告", fields, notion_api_key, template_db_id)
            if dry_run:
                logger.info("[DRY RUN] Would send warning: %s -> %s/%s (ws=%s)", title, channel_id, message_ts, workspace)
            else:
                if send_warning(client, channel_id, message_ts, message):
                    logger.info("[WARNED] Warning sent: %s (ws=%s)", title, workspace)
                    notion.update_status(page_id, "Approved", warning_sent_at=datetime.now(timezone.utc))
                else:
                    stats["errors"] += 1
                    continue
            stats["warned"] += 1
            continue

        # 警告送信日時あり → 閾値経過チェック → ステータスを 48h_Over に更新
        if not notion.is_past_threshold(fields["warning_sent_at"], hours_threshold):
            logger.info("[SKIP] Not yet %dh: %s", hours_threshold, title)
            stats["skipped_not_elapsed"] += 1
            continue

        if dry_run:
            logger.info("[DRY RUN] Would mark 48h_Over: %s", title)
            stats["expired"] += 1
        else:
            if notion.mark_48h_over(page_id):
                logger.info("[48h_OVER] Status updated: %s", title)
                stats["expired"] += 1
            else:
                logger.error("Failed to mark 48h_Over: %s", page_id)
                stats["errors"] += 1

    return stats


def process_remind_requests(
    slack: WebClient,
    notion: NotionClient,
    dry_run: bool = False,
    slack_clients: Optional[dict[str, WebClient]] = None,
    notion_api_key: str = "",
    template_db_id: str = "",
) -> dict[str, int]:
    """Remind_Requested のレコードを検知し、Slackに削除リマインドを送信

    運営がNotionで 48h_Over → Remind_Requested に変更したレコードを取得し、
    Slackスレッドに削除リマインドを送信後、ステータスを Reminded に更新する。

    Args:
        slack: デフォルトのSlack WebClient
        notion: NotionClient インスタンス
        dry_run: Trueの場合、実際の送信・更新をしない
        slack_clients: ワークスペース名 -> WebClient のマッピング
        notion_api_key: テンプレートDB取得用（空ならフォールバック使用）
        template_db_id: テンプレートDBのID（空ならフォールバック使用）
    """
    if slack_clients is None:
        slack_clients = {}

    stats: dict[str, int] = {
        "queried": 0,
        "reminded": 0,
        "skipped_no_link": 0,
        "already_deleted": 0,
        "errors": 0,
    }

    pages = notion.query_remind_requested()
    stats["queried"] = len(pages)
    logger.info("Found %d remind_requested records", len(pages))

    for page in pages:
        fields = notion.extract_reminder_fields(page)
        page_id: str = fields["page_id"]
        title: str = fields["title"][:TITLE_TRUNCATE_LEN]

        if fields.get("article_page_id"):
            article_name = notion.get_article_name(fields["article_page_id"])
            if article_name:
                fields["article_id"] = article_name

        parsed = notion.parse_slack_link(fields["post_link"])
        if not parsed:
            logger.warning("[SKIP] No valid post_link: %s", title)
            stats["skipped_no_link"] += 1
            if not dry_run:
                notion.mark_reminded(page_id)
            continue

        channel_id, message_ts, workspace = parsed
        client = _resolve_slack_client(workspace, slack_clients, slack)

        if not check_message_exists(client, channel_id, message_ts):
            logger.info("[DELETED] Message already deleted: %s", title)
            stats["already_deleted"] += 1
            if not dry_run:
                notion.mark_reminded(page_id)
            continue

        message = _build_message("リマインド", fields, notion_api_key, template_db_id)
        if dry_run:
            logger.info("[DRY RUN] Would send reminder: %s -> %s/%s (ws=%s)", title, channel_id, message_ts, workspace)
            stats["reminded"] += 1
        else:
            if send_reminder(client, channel_id, message_ts, message):
                logger.info("[REMINDED] Reminder sent: %s (ws=%s)", title, workspace)
                notion.mark_reminded(page_id)
                stats["reminded"] += 1
            else:
                stats["errors"] += 1

    return stats
