import json
import base64
from typing import Any
from slack_sdk import WebClient
from slack_sdk.signature import SignatureVerifier
from openai import OpenAI

# commonモジュールのインポート
from common.observability import build_context, log_info, log_error, emit_metric, Timer
from common.notion_client import NotionClient
from .services.config import load_config
from .services.moderation import run_moderation, encode_alert_button_value
from .services.models import severity_rank, ModerationResult

SERVICE = "app_inspect"

def lambda_handler(event: dict, context: Any) -> dict:
    # 1. コンテキスト初期化（この時点でSlackのIDなどが自動抽出される）
    ctx = build_context(event, context, service=SERVICE)
    total_timer = Timer()
    log_info(ctx, action="request_received")

    try:
        # 設定のロード
        cfg = load_config()

        # 2. ボディのパース
        body = event.get("body") or ""
        if event.get("isBase64Encoded"):
            body = base64.b64decode(body).decode("utf-8")

        try:
            body_json = json.loads(body)
        except Exception as e:
            # log_error の第3引数は例外オブジェクトそのものを渡す
            log_error(ctx, action="parse_json", error=e)
            return {"statusCode": 400, "body": "invalid json"}

        # 3. URL Verification (最優先)
        if body_json.get("type") == "url_verification":
            log_info(ctx, action="url_verification", result="success")
            return {
                "statusCode": 200,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"challenge": body_json.get("challenge", "")})
            }

        # 4. 署名検証
        verifier = SignatureVerifier(cfg.slack_signing_secret)
        headers = event.get("headers") or {}
        if not verifier.is_valid_request(body, headers):
            # 検証失敗は警告として記録
            log_info(ctx, action="verify_signature", result="fail", detail="invalid signature")
            return {"statusCode": 401, "body": "invalid signature"}

        # 5. イベントフィルタリング
        ev = body_json.get("event", {})
        # Bot自身の投稿などを無視
        if body_json.get("type") != "event_callback" or ev.get("type") != "message" or ev.get("bot_id") or ev.get("subtype"):
            return {"statusCode": 200, "body": "ignored"}

        text = ev.get("text", "").strip()
        if not text:
            return {"statusCode": 200, "body": "empty_text"}

        # 6. モデレーション実行
        log_info(ctx, action="start_moderation", text_length=len(text))
        inference_timer = Timer()
        
        if cfg.use_mock_openai:
            is_mock_violation = "違反" in text
            result = ModerationResult(
                is_violation=is_mock_violation,
                severity="medium",
                categories=["mock_test"],
                rationale="[MOCK] 違反ワード検知",
                recommended_reply="[MOCK] 削除を推奨します"
            )
        else:
            openai_client = OpenAI(api_key=cfg.openai_api_key)
            result = run_moderation(openai_client, cfg.openai_model, cfg.guidelines_text, text)
        
        emit_metric(ctx, "InferenceLatencyMs", inference_timer.ms(), unit="Milliseconds")

        if not result.is_violation or severity_rank(result.severity) < severity_rank(cfg.min_severity_to_alert):
            log_info(ctx, action="judge", result="not_violation")
            return {"statusCode": 200, "body": "ok"}

        # 7. 外部連携
        try:
            notion = NotionClient(cfg.notion_api_key, cfg.notion_db_id)
            slack_client = WebClient(token=cfg.slack_bot_token)
        
            # 投稿リンク
            permalink_resp = slack_client.chat_getPermalink(channel=ev["channel"], message_ts=ev["ts"])
            post_link = permalink_resp.get("permalink")

            notion_page_id = notion.create_violation_log(
                post_content=text,
                user_id=ev.get("user", "unknown"),
                channel=ev["channel"],
                result="Violation",
                method="OpenAI",
                reason=result.rationale,
                post_link=post_link
            )
            log_info(ctx, action="notion_page_created", page_id=notion_page_id)
        except Exception as e:
            log_error(ctx, action="external_service_call", error=e)
            notion_page_id = None

        # ボタンに現在の trace_id を埋め込む（管理者がボタンを押した時に追跡できるようにするため）
        button_value = encode_alert_button_value(
            notion_page_id=notion_page_id,
            trace_id=ctx.trace_id,
            origin_channel=ev["channel"],
            origin_ts=ev["ts"],
            reason=result.rationale,
        )

        blocks = [
            {"type": "section", "text": {"type": "mrkdwn", "text": f"🚨 *違反の可能性を検知*\n内容: {text[:50]}...\n理由: {result.rationale}"}},
            {"type": "actions", "elements": [
                {"type": "button", "text": {"type": "plain_text", "text": "削除勧告を送る"}, "style": "danger", "action_id": "approve_violation", "value": button_value}
            ]}
        ]

        slack_client.chat_postMessage(channel=cfg.alert_private_channel_id, text="【違反検知アラート】", blocks=blocks)
        
        log_info(ctx, action="alert_sent", result="success", page_id=notion_page_id)
        emit_metric(ctx, "TotalLatencyMs", total_timer.ms(), unit="Milliseconds")
        
        return {"statusCode": 200, "body": "ok"}

    except Exception as e:
        # e (例外オブジェクト) をそのまま渡すことで log_error の仕様に合わせる
        log_error(ctx, action="handler_process", error=e)
        emit_metric(ctx, "handler_error", 1)
        return {"statusCode": 200, "body": "error_handled"}