"""リマインドサービス: Notionポーリングで初回警告送信 + 48h経過通知 + 削除検知"""

import logging
import os
from datetime import datetime, timezone
from typing import Optional

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from common.notion_client import NotionClient
from common.secret_manager import get_parameter_by_name
from common.slack_utils import encode_alert_button_value
from common.template_manager import (
    resolve_template, render_template, compose_message, get_template_options,
)
from app_remind.components.slack_ui import build_48h_alert_blocks

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


def _resolve_slack_client(
    workspace: Optional[str],
    slack_clients: dict[str, WebClient],
    default_client: WebClient,
    team_id: Optional[str] = None,
) -> WebClient:
    """ワークスペース名またはteam_idからSlackクライアントを解決する

    優先順位:
    1. team_id → OAuthで保存されたper-teamトークン (SSM)
    2. workspace名 → 環境変数で設定されたワークスペース別トークン
    3. デフォルトクライアント
    """
    if team_id:
        prefix = os.getenv("SLACK_INSTALLATION_PARAM_PREFIX", "/slack/installation").rstrip("/")
        token = get_parameter_by_name(f"{prefix}/{team_id}/bot_token")
        if token:
            return WebClient(token=token)

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
        article=fields.get("article_name") or "",
        post_link=fields.get("post_link") or "",
        poster=fields.get("poster") or "",
    )
    return compose_message(rendered, fields.get("additional_message", ""))


def _send_48h_notification(
    slack: WebClient,
    notion: NotionClient,
    fields: dict,
    page_id: str,
    notion_api_key: str = "",
    template_db_id: str = "",
) -> None:
    """管理chの初回通知にスレッド返信で48h経過通知（ボタン付き）を送信"""
    admin_channel = fields["admin_channel_id"]
    admin_ts = fields["admin_message_ts"]

    # 再警告テンプレートの選択肢を取得
    rewarn_options = None
    if notion_api_key and template_db_id:
        try:
            raw = get_template_options(notion_api_key, template_db_id)
            if raw:
                rewarn_options = [
                    (o["name"], o["page_id"]) for o in raw if o.get("usage") == "再警告"
                ]
        except Exception as e:
            logger.error("Failed to fetch rewarn template options: %s", e)

    # 投稿リンクからchannel_id, message_tsを取得
    parsed = notion.parse_slack_link(fields.get("post_link"))
    origin_channel_id = parsed[0] if parsed else ""
    origin_ts = parsed[1] if parsed else ""

    button_value = encode_alert_button_value(
        notion_page_id=page_id,
        origin_channel=origin_channel_id,
        origin_ts=origin_ts,
        article_id=fields.get("article_name", ""),
    )

    blocks = build_48h_alert_blocks(
        post_link=fields.get("post_link"),
        post_text=fields.get("title", ""),
        poster=fields.get("poster"),
        origin_channel_id=origin_channel_id,
        button_value=button_value,
        rewarn_template_options=rewarn_options,
    )

    try:
        slack.chat_postMessage(
            channel=admin_channel,
            thread_ts=admin_ts,
            text="⏰ 48時間経過：投稿が未削除です",
            blocks=blocks,
        )
        logger.info("[48h_NOTIFIED] Thread reply sent to %s/%s", admin_channel, admin_ts)
    except SlackApiError as e:
        logger.error("Failed to send 48h notification: %s", e)


def _notify_deleted(slack: WebClient, fields: dict) -> None:
    """管理chのスレッドに「投稿が削除されました」を通知"""
    admin_channel = fields.get("admin_channel_id")
    admin_ts = fields.get("admin_message_ts")
    if not admin_channel or not admin_ts:
        return
    try:
        slack.chat_postMessage(
            channel=admin_channel,
            thread_ts=admin_ts,
            text="✅ 投稿が削除されました。対応ステータスを「対応終了」に更新しました。",
        )
        logger.info("[DELETED_NOTIFIED] Thread reply sent to %s/%s", admin_channel, admin_ts)
    except SlackApiError as e:
        logger.error("Failed to send deletion notification: %s", e)


def process_reminders(
    slack: WebClient,
    notion: NotionClient,
    hours_threshold: int = 48,
    dry_run: bool = False,
    slack_clients: Optional[dict[str, WebClient]] = None,
    notion_api_key: str = "",
    template_db_id: str = "",
) -> dict[str, int]:
    """Notionポーリングによる警告・48h経過通知・削除検知のメインロジック

    警告済み のレコードを取得し、以下を処理:
    - 投稿削除済み → 対応終了 + 管理ch通知
    - 警告送信日時が空 → 違反投稿スレッドに初回警告を送信 + 警告送信日時を記録
    - 警告送信日時あり + hours_threshold経過 → 期限超過 + 管理chスレッド返信（ボタン付き）

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
        "notified_48h": 0,
    }

    pages = notion.query_approved_unreminded()
    stats["queried"] = len(pages)
    logger.info("Found %d approved records", len(pages))

    for page in pages:
        fields = notion.extract_reminder_fields(page)
        page_id: str = fields["page_id"]
        title: str = fields["title"][:TITLE_TRUNCATE_LEN]

        # 対象条文 Relation から条文名を解決
        if fields.get("article_page_id"):
            article_name = notion.get_article_name(fields["article_page_id"])
            if article_name:
                fields["article_name"] = article_name

        parsed = notion.parse_slack_link(fields["post_link"])
        if not parsed:
            logger.warning("[SKIP] No valid post_link: %s", title)
            stats["skipped_no_link"] += 1
            continue

        channel_id, message_ts, workspace = parsed
        client = _resolve_slack_client(workspace, slack_clients, slack, team_id=fields.get("team_id"))

        if not check_message_exists(client, channel_id, message_ts):
            logger.info("[DELETED] Message already deleted: %s", title)
            stats["already_deleted"] += 1
            if not dry_run:
                notion.mark_closed(page_id)
                _notify_deleted(client, fields)
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
                    notion.update_status(page_id, "警告済み", warning_sent_at=datetime.now(timezone.utc))
                else:
                    stats["errors"] += 1
                    continue
            stats["warned"] += 1
            continue

        # 警告送信日時あり → 閾値経過チェック → ステータスを 期限超過 に更新 + 管理ch通知
        if not notion.is_past_threshold(fields["warning_sent_at"], hours_threshold):
            logger.info("[SKIP] Not yet %dh: %s", hours_threshold, title)
            stats["skipped_not_elapsed"] += 1
            continue

        if dry_run:
            logger.info("[DRY RUN] Would mark 期限超過: %s", title)
            stats["expired"] += 1
        else:
            if notion.mark_48h_over(page_id):
                logger.info("[48h_OVER] Status updated: %s", title)
                stats["expired"] += 1

                # 管理chの初回通知にスレッド返信で48h通知を送信
                if fields.get("admin_channel_id") and fields.get("admin_message_ts"):
                    _send_48h_notification(
                        client, notion, fields, page_id,
                        notion_api_key=notion_api_key,
                        template_db_id=template_db_id,
                    )
                    stats["notified_48h"] += 1
            else:
                logger.error("Failed to mark 期限超過: %s", page_id)
                stats["errors"] += 1

    return stats


def process_deletion_check(
    slack: WebClient,
    notion: NotionClient,
    dry_run: bool = False,
    slack_clients: Optional[dict[str, WebClient]] = None,
) -> dict[str, int]:
    """全アクティブレコード（警告済み・期限超過・再警告済み）の削除チェック

    投稿が削除されていたら対応終了に更新し、管理chに通知する。
    process_remindersの削除チェックは警告済みのみ対象なので、
    期限超過・再警告済みはこの関数でカバーする。
    """
    if slack_clients is None:
        slack_clients = {}

    stats: dict[str, int] = {
        "queried": 0,
        "deleted": 0,
        "alive": 0,
        "skipped_no_link": 0,
        "errors": 0,
    }

    pages = notion.query_active_violations()
    stats["queried"] = len(pages)
    logger.info("Found %d active violations for deletion check", len(pages))

    for page in pages:
        fields = notion.extract_reminder_fields(page)
        page_id: str = fields["page_id"]
        title: str = fields["title"][:TITLE_TRUNCATE_LEN]

        parsed = notion.parse_slack_link(fields["post_link"])
        if not parsed:
            stats["skipped_no_link"] += 1
            continue

        channel_id, message_ts, workspace = parsed
        client = _resolve_slack_client(
            workspace, slack_clients, slack, team_id=fields.get("team_id"),
        )

        if check_message_exists(client, channel_id, message_ts):
            stats["alive"] += 1
            continue

        logger.info("[DELETED] Message deleted: %s", title)
        stats["deleted"] += 1
        if not dry_run:
            notion.mark_closed(page_id)
            _notify_deleted(client, fields)

    return stats


def process_rewarn_from_notion(
    slack: WebClient,
    notion: NotionClient,
    dry_run: bool = False,
    slack_clients: Optional[dict[str, WebClient]] = None,
    notion_api_key: str = "",
    template_db_id: str = "",
) -> dict[str, int]:
    """Notion手動ルート: 再警告済み + 再警告送信日時空のレコードを処理

    運営がNotionで対応ステータスを「再警告済み」に変更したレコードを検知し、
    テンプレート + 追加メッセージを結合してSlackに再警告を送信する。
    """
    if slack_clients is None:
        slack_clients = {}

    stats: dict[str, int] = {
        "queried": 0,
        "sent": 0,
        "skipped_no_link": 0,
        "already_deleted": 0,
        "errors": 0,
    }

    pages = notion.query_rewarn_unsent()
    stats["queried"] = len(pages)
    logger.info("Found %d rewarn_unsent records", len(pages))

    for page in pages:
        fields = notion.extract_reminder_fields(page)
        page_id: str = fields["page_id"]
        title: str = fields["title"][:TITLE_TRUNCATE_LEN]

        # 対象条文 Relation から条文名を解決
        if fields.get("article_page_id"):
            article_name = notion.get_article_name(fields["article_page_id"])
            if article_name:
                fields["article_name"] = article_name

        parsed = notion.parse_slack_link(fields["post_link"])
        if not parsed:
            logger.warning("[SKIP] No valid post_link: %s", title)
            stats["skipped_no_link"] += 1
            continue

        channel_id, message_ts, workspace = parsed
        client = _resolve_slack_client(
            workspace, slack_clients, slack, team_id=fields.get("team_id"),
        )

        if not check_message_exists(client, channel_id, message_ts):
            logger.info("[DELETED] Message already deleted: %s", title)
            stats["already_deleted"] += 1
            if not dry_run:
                notion.mark_closed(page_id)
                _notify_deleted(client, fields)
            continue

        # 再警告テンプレートRelationがあればそれを使用、なければデフォルト
        fields["template_page_id"] = fields.get("rewarn_template_page_id", "")
        message = _build_message("再警告", fields, notion_api_key, template_db_id)

        if dry_run:
            logger.info(
                "[DRY RUN] Would send rewarn: %s -> %s/%s (ws=%s)",
                title, channel_id, message_ts, workspace,
            )
            stats["sent"] += 1
        else:
            if _send_thread_message(client, channel_id, message_ts, message, "rewarn"):
                logger.info("[REWARNED] Rewarn sent: %s (ws=%s)", title, workspace)
                notion.update_rewarn_info(
                    page_id,
                    rewarn_sent_at=datetime.now(timezone.utc),
                    template_page_id=fields.get("rewarn_template_page_id", ""),
                )
                stats["sent"] += 1
            else:
                stats["errors"] += 1

    return stats
