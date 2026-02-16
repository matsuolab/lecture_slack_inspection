import json
import base64
import os
from typing import Any
from urllib.parse import parse_qs
from slack_sdk import WebClient
from slack_sdk.signature import SignatureVerifier

# 共通モジュールのインポート
from common.observability import build_context, log_info, log_error, emit_metric, Timer
from common.notion_client import NotionClient
from .services.config import load_config

SERVICE = "app_alert"

def lambda_handler(event: dict, context: Any) -> dict:
    # 1. ログコンテキストの初期化（ボタン内の trace_id も自動で引き継がれる仕様）
    ctx = build_context(event, context, service=SERVICE)
    total_timer = Timer()
    log_info(ctx, action="request_received")

    try:
        # 2. Configのロード (環境変数ではなく load_config に一本化)
        # これにより KeyError: 'SLACK_BOT_TOKEN' が解消されます
        cfg = load_config()

        # 3. 署名検証用の準備
        verifier = SignatureVerifier(cfg.slack_signing_secret)
        headers = event.get("headers") or {}
        raw_body = event.get("body") or ""
        if event.get("isBase64Encoded"):
            raw_body = base64.b64decode(raw_body).decode("utf-8")

        # 4. ペイロードの解析
        try:
            # Slack Interactivityは payload=... という形式で届く
            if "payload=" in raw_body:
                decoded = parse_qs(raw_body)
                payload = json.loads(decoded["payload"][0])
            else:
                payload = json.loads(raw_body)
            log_info(ctx, action="parse_payload", result="success")
        except Exception as e:
            log_error(ctx, action="parse_payload", error=e)
            return {"statusCode": 400, "body": "Bad Request"}

        # 5. 署名検証の実行
        if not verifier.is_valid_request(raw_body, headers):
            log_info(ctx, action="verify_signature", result="fail")
            return {"statusCode": 401, "body": "Invalid signature"}

        # 6. アクションの特定
        actions = payload.get("actions", [])
        if not actions:
            log_info(ctx, action="ignore_action", action_id=None)
            return {"statusCode": 200, "body": "OK"}

        action_id = actions[0].get("action_id")
        if action_id not in ("approve_violation", "dismiss_violation"):
            log_info(ctx, action="ignore_action", action_id=action_id)
            return {"statusCode": 200, "body": "OK"}


        # ボタンの value に埋め込まれた情報を復元
        try:
            action_val = json.loads(actions[0].get("value", "{}"))
            origin_channel = action_val.get("origin_channel")
            origin_ts = action_val.get("origin_ts")
            notion_page_id = action_val.get("notion_page_id")
            log_info(ctx, action="extract_value", notion_page_id=notion_page_id)
        except Exception as e:
            log_error(ctx, action="extract_value", error=e)
            return {"statusCode": 200, "body": "Value parse error"}

        if action_id == "approve_violation" and (not origin_channel or not origin_ts):
            log_info(ctx, action="missing_origin_info", result="stop")
            return {"statusCode": 200, "body": "Missing origin info"}

        # 7. 外部サービス実行
        slack = WebClient(token=cfg.slack_bot_token)
        notion = NotionClient(cfg.notion_api_key, cfg.notion_db_id)

        container = payload.get("container", {})
        admin_channel_id = container.get("channel_id")
        admin_message_ts = container.get("message_ts")

        if action_id == "approve_violation":
            # ユーザーへ警告返信（スレッドに返信）
            log_info(ctx, action="post_warning_to_user", channel=origin_channel)
            slack.chat_postMessage(
                channel=origin_channel,
                thread_ts=origin_ts,
                text=cfg.reply_prefix
            )

            # Notionステータス更新
            if notion_page_id:
                log_info(ctx, action="update_notion", page_id=notion_page_id, status="Approved")
                notion.update_status(notion_page_id, "Approved")

            # 管理者メッセージ更新（完了表示）
            if admin_channel_id and admin_message_ts:
                log_info(ctx, action="update_admin_message", status="Approved")
                slack.chat_update(
                    channel=admin_channel_id,
                    ts=admin_message_ts,
                    text="Approved",
                    blocks=[
                        {"type": "section", "text": {"type": "mrkdwn", "text": "✅ *対応完了* （警告送信済み）"}}
                    ]
                )

            return {"statusCode": 200, "body": "OK"}

        elif action_id == "dismiss_violation":
            # dismiss は「ユーザーへの返信はしない」＋ Notion status を dismiss に更新
            if not notion_page_id:
                log_info(ctx, action="missing_notion_page_id", result="stop")
                return {"statusCode": 200, "body": "Missing notion_page_id"}

            log_info(ctx, action="update_notion", page_id=notion_page_id, status="Dismissed")
            notion.update_status(notion_page_id, "Dismissed")

            # 管理者メッセージ更新（却下表示）
            if admin_channel_id and admin_message_ts:
                log_info(ctx, action="update_admin_message", status="Dismissed")
                slack.chat_update(
                    channel=admin_channel_id,
                    ts=admin_message_ts,
                    text="Dismissed",
                    blocks=[
                        {"type": "section", "text": {"type": "mrkdwn", "text": "🚫 *Dismissed* （対応不要）"}}
                    ]
                )

            return {"statusCode": 200, "body": "OK"}



    except Exception as e:
        log_error(ctx, action="handler_failed", error=e)
        emit_metric(ctx, "AlertActionError", 1)
        return {"statusCode": 200, "body": "error_handled"}