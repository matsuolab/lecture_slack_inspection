import json
import base64
from typing import Any
from slack_sdk import WebClient
from slack_sdk.signature import SignatureVerifier
from openai import OpenAI

from common.observability import build_context, log_info, log_error, emit_metric, Timer
from common.notion_client import NotionClient
from common.template_manager import get_template_options
from .services.config import load_config, load_signing_secret
from .services.moderation import run_moderation
from .components.slack_builder import encode_alert_button_value
from .components.slack_ui import build_violation_alert_blocks
from .services.models import severity_rank, ModerationResult

SERVICE = "app_inspect"

def _decode_body(event: dict) -> str:
    body = event.get("body", "") or ""
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")
    return body

def lambda_handler(event: dict, context: Any) -> dict:
    context = build_context(event, context, service=SERVICE)
    total_timer = Timer()
    log_info(context, action="request_received")

    try:
        # Slackリトライ検出（3秒タイムアウト時の再送を即返却）
        raw_headers = event.get("headers") or {}
        lower_headers = {k.lower(): v for k, v in raw_headers.items()}
        if lower_headers.get("x-slack-retry-num"):
            log_info(context, action="retry_skip", retry_num=lower_headers["x-slack-retry-num"])
            return {"statusCode": 200, "body": "ok"}

        body = _decode_body(event)
        headers = event.get("headers") or {}
        verifier = SignatureVerifier(load_signing_secret())
        if not verifier.is_valid_request(body, headers):
            log_info(context, action="verify_signature", result="fail", detail="invalid signature")
            return {"statusCode": 401, "body": "invalid signature"}

        try:
            body_json = json.loads(body)
        except Exception as e:
            log_error(context, action="parse_json", error=e)
            return {"statusCode": 400, "body": "invalid json"}

        # URL Verification
        if body_json.get("type") == "url_verification":
            log_info(context, action="url_verification", result="success")
            return {"statusCode": 200, "headers": {"Content-Type": "application/json"}, "body": json.dumps({"challenge": body_json.get("challenge", "")})}

        team_id = body_json.get("team_id") or context.slack_team_id
        if not team_id:
            log_info(context, action="missing_team_id", result="fail")
            return {"statusCode": 400, "body": "missing team_id"}
        
        cfg = load_config(team_id)
        ev = body_json.get("event", {})
        if body_json.get("type") != "event_callback" or ev.get("type") != "message" or ev.get("bot_id") or ev.get("subtype"):
            return {"statusCode": 200, "body": "ignored"}

        text = ev.get("text", "").strip()
        if not text:
            return {"statusCode": 200, "body": "empty_text"}

        # モデレーション実行
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
            result = run_moderation(openai_client, cfg.openai_model, cfg.guidelines_text, text)

        emit_metric(context, "InferenceLatencyMs", inference_timer.ms(), unit="Milliseconds")

        if not result.is_violation or severity_rank(result.severity) < severity_rank(cfg.min_severity_to_alert):
            log_info(context, action="judge", result="not_violation")
            return {"statusCode": 200, "body": "ok"}

        # 外部連携（Slack名前解決 + Notion記録 + アラート送信）
        slack_client = WebClient(token=cfg.slack_bot_token)

        raw_user_id = ev.get("user", "")
        raw_channel_id = ev.get("channel", "")
        raw_team_id = body_json.get("team_id", "")

        profile_name = f"@{raw_user_id}"
        channel_name = raw_channel_id
        workspace_name = raw_team_id
        post_link = None
        notion_page_id = None

        try:
            notion = NotionClient(cfg.notion_api_key, cfg.notion_db_id, cfg.notion_articles_db_id)

            # Slack APIで名前を解決する
            try:
                if raw_user_id:
                    u_res = slack_client.users_info(user=raw_user_id)
                    p = u_res["user"]["profile"]
                    profile_name = "@" + (p.get("display_name") or p.get("real_name") or raw_user_id)

                if raw_channel_id:
                    c_res = slack_client.conversations_info(channel=raw_channel_id)
                    channel_name = c_res["channel"]["name"]

                if raw_team_id:
                    t_res = slack_client.team_info(team=raw_team_id)
                    workspace_name = t_res["team"]["name"]
            except Exception as e:
                log_error(context, action="fetch_slack_names", error=e)

            # 重複チェック（同じ投稿の二重処理防止）
            if notion.check_duplicate_violation(ev["ts"]):
                log_info(context, action="duplicate_skip", message_ts=ev["ts"])
                return {"statusCode": 200, "body": "duplicate"}

            # 投稿リンク
            permalink_resp = slack_client.chat_getPermalink(channel=ev["channel"], message_ts=ev["ts"])
            post_link = permalink_resp.get("permalink")

            notion_page_id = notion.create_violation_log(
                post_content=text,
                user_id=profile_name,
                channel=channel_name,
                workspace=workspace_name,
                result="Violation",
                method=getattr(result, "method", None) or "LLM",
                reason=result.rationale,
                severity=result.severity,
                categories=result.categories,
                post_link=post_link,
                article_id=result.article_id,
                confidence=result.confidence,
                message_ts=ev["ts"],
                team_id=team_id,
            )
            log_info(context, action="notion_page_created", page_id=notion_page_id)

            if notion_page_id and result.article_id:
                article_page_id = notion.find_article_page_id(result.article_id)
                if article_page_id:
                    notion.set_article_relation(notion_page_id, article_page_id)

        except Exception as e:
            log_error(context, action="external_service_call", error=e)
            notion_page_id = None

        # アラートボタンに埋め込むペイロード
        button_value = encode_alert_button_value(
            notion_page_id=notion_page_id,
            trace_id=context.trace_id,
            origin_channel=ev["channel"],
            origin_ts=ev["ts"],
            reason=result.rationale,
            article_id=result.article_id,
        )

        # 検出方法に基づいて確信度の表示有無を決定
        if cfg.use_mock_openai:
            detection_method = "Mock"
            confidence_for_ui = None
        else:
            detection_method = getattr(result, "method", None) or "LLM"
            confidence_for_ui = result.confidence if detection_method == "LLM" else None

        # テンプレートDBからプルダウン選択肢を動的生成
        template_options = None
        if cfg.notion_template_db_id:
            try:
                raw_options = get_template_options(cfg.notion_api_key, cfg.notion_template_db_id)
                if raw_options:
                    template_options = [(o["name"], o["page_id"]) for o in raw_options]
            except Exception as e:
                log_error(context, action="fetch_template_options", error=e)

        blocks = build_violation_alert_blocks(
            author_user_id=(raw_user_id or None),
            author_display=(profile_name or None),
            origin_channel_id=raw_channel_id,
            post_link=post_link,
            post_ts=ev.get("ts"),
            post_text=text,
            detection_method=detection_method,
            confidence=confidence_for_ui,
            rationale=result.rationale,
            guideline_article=result.article_id,
            categories=result.categories,
            button_value=button_value,
            warning_template_options=template_options,
        )

        slack_client.chat_postMessage(
            channel=cfg.alert_private_channel_id,
            text="【違反検知アラート】",
            blocks=blocks
        )

        log_info(context, action="alert_sent", result="success", page_id=notion_page_id)
        emit_metric(context, "TotalLatencyMs", total_timer.ms(), unit="Milliseconds")

        return {"statusCode": 200, "body": "ok"}

    except Exception as e:
        log_error(context, action="handler_process", error=e)
        emit_metric(context, "handler_error", 1)
        return {"statusCode": 200, "body": "error_handled"}
