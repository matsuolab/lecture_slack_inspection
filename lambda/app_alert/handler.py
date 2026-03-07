import json
import base64
from typing import Any
from urllib.parse import parse_qs
from slack_sdk import WebClient
from slack_sdk.signature import SignatureVerifier

from common.observability import build_context, log_info, log_error, emit_metric, Timer
from common.notion_client import NotionClient
from .services.config import load_config, load_signing_secret
from .services.actions import parse_action_context, handle_approve_violation, handle_dismiss_violation

SERVICE = "app_alert"

def lambda_handler(event: dict, context: Any) -> dict:
    context = build_context(event, context, service=SERVICE)
    total_timer = Timer()
    log_info(context, action="request_received")

    try:
        headers = event.get("headers") or {}
        raw_body = event.get("body") or ""
        if event.get("isBase64Encoded"):
            raw_body = base64.b64decode(raw_body).decode("utf-8")

        verifier = SignatureVerifier(load_signing_secret())
        if not verifier.is_valid_request(raw_body, headers):
            log_info(context, action="verify_signature", result="fail", detail="invalid signature")
            return {"statusCode": 401, "body": "invalid signature"}
        
        try:
            if "payload=" in raw_body:
                decoded = parse_qs(raw_body)
                payload = json.loads(decoded["payload"][0])
            else:
                payload = json.loads(raw_body)
            log_info(context, action="parse_payload", result="success")
        except Exception as e:
            log_error(context, action="parse_payload", error=e)
            return {"statusCode": 400, "body": "Bad Request"}
        
        team_id = (payload.get("team", {}).get("id")) or context.slack_team_id
        if not team_id:
            log_info(context, action="missing_team_id", result="fail")
            return {"statusCode": 400, "body": "missing team_id"}

        # 2. コンテキスト解析
        cfg = load_config(team_id)
        action_context = parse_action_context(payload)

        # 対応者（ボタン押下者）
        responder_id = None
        responder_name = None
        user_info = payload.get("user")
        if isinstance(user_info, dict):
            responder_id = user_info.get("id")
            responder_name = user_info.get("name") or user_info.get("username") or responder_id

        if not action_context or not action_context.action_id:
            log_info(context, action="ignore_action", reason="no_action_id")
            return {"statusCode": 200, "body": "OK"}

        if action_context.action_id not in ("approve_violation", "dismiss_violation"):
            log_info(context, action="ignore_action", action_id=action_context.action_id)
            return {"statusCode": 200, "body": "OK"}

        # 3. クライアント初期化
        slack = WebClient(token=cfg.slack_bot_token)
        notion = NotionClient(cfg.notion_api_key, cfg.notion_db_id)

        success = False
        
        page_id = action_context.value.get("notion_page_id")

        if action_context.action_id == "approve_violation":
            log_info(context, action="exec_approve", page_id=page_id)
            
            success = handle_approve_violation(
                context=action_context,
                slack=slack,
                notion=notion,
                reply_text=cfg.reply_prefix,
                responder_id=responder_id,
                responder_name=responder_name,
            )

        elif action_context.action_id == "dismiss_violation":
            log_info(context, action="exec_dismiss", page_id=page_id)

            success = handle_dismiss_violation(
                context=action_context,
                slack=slack,
                notion=notion,
                responder_id=responder_id,
                responder_name=responder_name,
            )

        if success:
            emit_metric(context, "ActionSuccess", 1)
            return {"statusCode": 200, "body": "OK"}

        emit_metric(context, "ActionFailure", 1)
        return {"statusCode": 200, "body": "Action Failed"}

    except Exception as e:
        log_error(context, action="handler_failed", error=e)
        emit_metric(context, "AlertActionError", 1)
        return {"statusCode": 200, "body": "error_handled"}
