import base64
import json
from typing import Any, Callable

from openai import OpenAI
from slack_sdk import WebClient
from slack_sdk.signature import SignatureVerifier

from common.health import write_health
from common.notion_client import NotionClient
from common.observability import Timer, build_context, emit_metric, log_error, log_info

from .components.slack_event_parser import parse_slack_message_event
from .services.config import load_config, load_signing_secret
from .services.inspection_flow import handle_edited_message, handle_new_message
from .services.moderation import run_moderation
from .services.models import ModerationResult

SERVICE = "app_inspect"


def _decode_body(event: dict) -> str:
    body = event.get("body", "") or ""
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")
    return body


def _build_moderation_executor(context: Any, cfg: Any) -> Callable[[str], ModerationResult]:
    def _moderate(text: str) -> ModerationResult:
        log_info(context, action="start_moderation", text_length=len(text))
        inference_timer = Timer()

        if cfg.use_mock_openai:
            is_mock_violation = "違反" in text
            result = ModerationResult(
                is_violation=is_mock_violation,
                severity="medium",
                categories=["mock_test"],
                rationale="[MOCK] 違反ワード検知",
                recommended_reply="[MOCK] 削除を推奨します",
                confidence=0.9,
                article_id="mock_article_123",
                method="Mock",
            )
        else:
            openai_client = OpenAI(api_key=cfg.openai_api_key)
            result = run_moderation(
                openai_client,
                cfg.openai_model,
                cfg.guidelines_text,
                text,
            )

        emit_metric(context, "InferenceLatencyMs", inference_timer.ms(), unit="Milliseconds")
        return result

    return _moderate


def lambda_handler(event: dict, context: Any) -> dict:
    context = build_context(event, context, service=SERVICE)
    total_timer = Timer()
    log_info(context, action="request_received")

    try:
        raw_headers = event.get("headers") or {}
        lower_headers = {k.lower(): v for k, v in raw_headers.items()}
        if lower_headers.get("x-slack-retry-num"):
            log_info(
                context,
                action="retry_skip",
                retry_num=lower_headers["x-slack-retry-num"],
            )
            return {"statusCode": 200, "body": "ok"}

        body = _decode_body(event)
        headers = event.get("headers") or {}
        verifier = SignatureVerifier(load_signing_secret())
        if not verifier.is_valid_request(body, headers):
            log_info(
                context,
                action="verify_signature",
                result="fail",
                detail="invalid signature",
            )
            return {"statusCode": 401, "body": "invalid signature"}

        try:
            body_json = json.loads(body)
        except Exception as e:
            log_error(context, action="parse_json", error=e)
            return {"statusCode": 400, "body": "invalid json"}

        if body_json.get("type") == "url_verification":
            log_info(context, action="url_verification", result="success")
            return {
                "statusCode": 200,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"challenge": body_json.get("challenge", "")}),
            }

        team_id = body_json.get("team_id") or context.slack_team_id
        if not team_id:
            log_info(context, action="missing_team_id", result="fail")
            return {"statusCode": 400, "body": "missing team_id"}

        inspect_event = parse_slack_message_event(body_json, default_team_id=team_id)
        if inspect_event is None:
            return {"statusCode": 200, "body": "ignored"}

        cfg = load_config(team_id)
        slack_client = WebClient(token=cfg.slack_bot_token)
        notion = NotionClient(cfg.notion_api_key, cfg.notion_db_id, cfg.notion_articles_db_id)
        moderate_text = _build_moderation_executor(context, cfg)

        if inspect_event.kind == "new_message":
            response = handle_new_message(
                context=context,
                cfg=cfg,
                inspect_event=inspect_event,
                slack_client=slack_client,
                notion=notion,
                moderate_text=moderate_text,
            )
        else:
            response = handle_edited_message(
                context=context,
                cfg=cfg,
                inspect_event=inspect_event,
                slack_client=slack_client,
                notion=notion,
                moderate_text=moderate_text,
            )

        emit_metric(context, "TotalLatencyMs", total_timer.ms(), unit="Milliseconds")
        write_health(SERVICE, "正常", notion_api_key=cfg.notion_api_key)
        return response

    except Exception as e:
        log_error(context, action="handler_process", error=e)
        emit_metric(context, "handler_error", 1)
        write_health(SERVICE, "エラー", str(e))
        return {"statusCode": 200, "body": "error_handled"}