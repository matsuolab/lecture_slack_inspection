import json
import base64
import os
from typing import Any
from urllib.parse import parse_qs
from slack_sdk import WebClient
from slack_sdk.signature import SignatureVerifier
from common.observability import build_context, log_info, log_error, emit_metric, Timer
from common.notion_client import NotionClient
from .services.config import load_config

# actions.py からロジックとパース関数をインポート
from .services.actions import parse_action_context, handle_approve_violation, handle_dismiss_violation

SERVICE = "app_alert"

#---------------以下、変更点なし------------------
def lambda_handler(event: dict, context: Any) -> dict:
    ctx = build_context(event, context, service=SERVICE)
    total_timer = Timer()
    log_info(ctx, action="request_received")

    try:

        cfg = load_config()

        verifier = SignatureVerifier(cfg.slack_signing_secret)
        headers = event.get("headers") or {}
        raw_body = event.get("body") or ""
        if event.get("isBase64Encoded"):
            raw_body = base64.b64decode(raw_body).decode("utf-8")

        try:
            if "payload=" in raw_body:
                decoded = parse_qs(raw_body)
                payload = json.loads(decoded["payload"][0])
            else:
                payload = json.loads(raw_body)
            log_info(ctx, action="parse_payload", result="success")
        except Exception as e:
            log_error(ctx, action="parse_payload", error=e)
            return {"statusCode": 400, "body": "Bad Request"}

        if not verifier.is_valid_request(raw_body, headers):
            log_info(ctx, action="verify_signature", result="fail")
            return {"statusCode": 401, "body": "Invalid signature"}

        
        # ------------以下、変更あり-----------

        
        # アクションコンテキストの生成
        action_ctx = parse_action_context(payload)
        if not action_ctx or not action_ctx.action_id:
            log_info(ctx, action="ignore_action", reason="no_action_id")
            return {"statusCode": 200, "body": "OK"}

        if action_ctx.action_id not in ("approve_violation", "dismiss_violation"):
            log_info(ctx, action="ignore_action", action_id=action_ctx.action_id)
            return {"statusCode": 200, "body": "OK"}

        # 外部サービスクライアントの初期化
        slack = WebClient(token=cfg.slack_bot_token)
        notion = NotionClient(cfg.notion_api_key, cfg.notion_db_id)

        # 以前の if/elif ブロックを関数呼び出しに置き換えました
        
        success = False
        
        if action_ctx.action_id == "approve_violation":
            log_info(ctx, action="exec_approve", page_id=action_ctx.notion_page_id)
            success = handle_approve_violation(
                ctx=action_ctx, 
                slack=slack, 
                notion=notion, 
                reply_text=cfg.reply_prefix
            )

        elif action_ctx.action_id == "dismiss_violation":
            log_info(ctx, action="exec_dismiss", page_id=action_ctx.value.get("notion_page_id"))
            success = handle_dismiss_violation(
                ctx=action_ctx, 
                slack=slack, 
                notion=notion
            )

       #--------------以下、変更点なし---------------------

        if success:
            emit_metric(ctx, "ActionSuccess", 1)
            return {"statusCode": 200, "body": "OK"}
        else:
            emit_metric(ctx, "ActionFailed", 1)
            # 失敗時もSlackへは200を返すのが基本（リトライループ防止のため要検討だが、元のコードに従う）
            return {"statusCode": 200, "body": "Action Failed"}

    except Exception as e:
        log_error(ctx, action="handler_failed", error=e)
        emit_metric(ctx, "AlertActionError", 1)
        return {"statusCode": 200, "body": "error_handled"}
