"""リマインドサービス: Notionポーリングで 48h経過通知 + 削除検知"""

import logging
import os
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
    """元投稿がまだ存在するか確認（tombstone=削除済みも検出）"""
    try:
        resp = slack.conversations_history(
            channel=channel_id,
            oldest=message_ts,
            latest=message_ts,
            inclusive=True,
            limit=1,
        )
        messages = resp.get("messages", [])
        if not messages or messages[0].get("ts") != message_ts:
            return False
        # スレッド返信があると削除後もtombstoneとして残る
        if messages[0].get("subtype") == "tombstone":
            return False
        return True
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


def _resolve_slack_client(
    workspace: Optional[str],
    slack_clients: dict[str, WebClient],
    team_id: Optional[str] = None,
) -> Optional[WebClient]:
    """ワークスペース名またはteam_idからSlackクライアントを解決する

    優先順位:
    1. team_id → OAuthで保存されたper-teamトークン (SSM)
    2. workspace名 → 環境変数で設定されたワークスペース別トークン

    どちらも解決できなければ None を返す (呼び出し側で skip 判定する)。
    """
    if team_id:
        prefix = os.getenv("SLACK_INSTALLATION_PARAM_PREFIX", "/slack/installation").rstrip("/")
        token = get_parameter_by_name(f"{prefix}/{team_id}/bot_token")
        if token:
            return WebClient(token=token)

    if workspace and workspace in slack_clients:
        return slack_clients[workspace]

    logger.warning("[SKIP] No Slack client resolvable (team_id=%s workspace=%s)", team_id, workspace)
    return None


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
    notion: NotionClient,
    hours_threshold: int = 48,
    dry_run: bool = False,
    slack_clients: Optional[dict[str, WebClient]] = None,
    notion_api_key: str = "",
    template_db_id: str = "",
) -> dict[str, int]:
    """Notionポーリングによる 48h経過通知・削除検知のメインロジック

    警告済み のレコードを取得し、以下を処理:
    - 投稿削除済み → 対応終了 + 管理ch通知
    - 警告送信日時あり + hours_threshold経過 → 期限超過 + 管理chスレッド返信（ボタン付き）

    Args:
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
        "skipped_not_elapsed": 0,
        "skipped_no_link": 0,
        "skipped_no_client": 0,
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
        client = _resolve_slack_client(workspace, slack_clients, team_id=fields.get("team_id"))
        if client is None:
            stats["skipped_no_client"] += 1
            continue

        if not check_message_exists(client, channel_id, message_ts):
            logger.info("[DELETED] Message already deleted: %s", title)
            stats["already_deleted"] += 1
            if not dry_run:
                notion.mark_closed(page_id)
                _notify_deleted(client, fields)
            continue

        # 警告送信日時が空のレコードはスキップ（Slackボタン未押下 or Notion手動で状態変更された異常系）
        if fields["warning_sent_at"] is None:
            logger.info("[SKIP] warning_sent_at is empty: %s", title)
            stats["skipped_not_elapsed"] += 1
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
        "skipped_no_client": 0,
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
            workspace, slack_clients, team_id=fields.get("team_id"),
        )
        if client is None:
            stats["skipped_no_client"] += 1
            continue

        if check_message_exists(client, channel_id, message_ts):
            stats["alive"] += 1
            continue

        logger.info("[DELETED] Message deleted: %s", title)
        stats["deleted"] += 1
        if not dry_run:
            notion.mark_closed(page_id)
            _notify_deleted(client, fields)

    return stats


