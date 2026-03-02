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
from .services.moderation import run_moderation
from .components.slack_builder import encode_alert_button_value
from .components.slack_ui import build_violation_alert_blocks
from .services.models import severity_rank, ModerationResult

SERVICE = "app_inspect"

def lambda_handler(event: dict, context: Any) -> dict:
    # 1. コンテキスト初期化（この時点でSlackのIDなどが自動抽出される）
    context = build_context(event, context, service=SERVICE)
    total_timer = Timer()
    log_info(context, action="request_received")

    try:
        # 1.5 Slackリトライ検出（3秒タイムアウト時の再送を即返却）
        raw_headers = event.get("headers") or {}
        lower_headers = {k.lower(): v for k, v in raw_headers.items()}
        if lower_headers.get("x-slack-retry-num"):
            log_info(context, action="retry_skip", retry_num=lower_headers["x-slack-retry-num"])
            return {"statusCode": 200, "body": "ok"}

        # 設定のロード
        cfg = load_config()

        # 2. ボディのパース
        body = event.get("body") or ""
        if event.get("isBase64Encoded"):
            body = base64.b64decode(body).decode("utf-8")

        try:
            body_json = json.loads(body)
        except Exception as e:
            log_error(context, action="parse_json", error=e)
            return {"statusCode": 400, "body": "invalid json"}

        # 3. URL Verification (最優先)
        if body_json.get("type") == "url_verification":
            log_info(context, action="url_verification", result="success")
            return {
                "statusCode": 200,
                "headers": {"Content-Type": "application/json"},
                "body": json.dumps({"challenge": body_json.get("challenge", "")})
            }

        # 4. 署名検証
        verifier = SignatureVerifier(cfg.slack_signing_secret)
        headers = event.get("headers") or {}
        if not verifier.is_valid_request(body, headers):
            log_info(context, action="verify_signature", result="fail", detail="invalid signature")
            return {"statusCode": 401, "body": "invalid signature"}

        # 5. イベントフィルタリング
        ev = body_json.get("event", {})
        if body_json.get("type") != "event_callback" or ev.get("type") != "message" or ev.get("bot_id") or ev.get("subtype"):
            return {"statusCode": 200, "body": "ignored"}

        text = ev.get("text", "").strip()
        if not text:
            return {"statusCode": 200, "body": "empty_text"}

        # 6. モデレーション実行
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
                article_id="mock_article_123"
            )
        else:
            openai_client = OpenAI(api_key=cfg.openai_api_key)
            result = run_moderation(openai_client, cfg.openai_model, cfg.guidelines_text, text)

        emit_metric(context, "InferenceLatencyMs", inference_timer.ms(), unit="Milliseconds")

        if not result.is_violation or severity_rank(result.severity) < severity_rank(cfg.min_severity_to_alert):
            log_info(context, action="judge", result="not_violation")
            return {"statusCode": 200, "body": "ok"}

        # 7. 外部連携
        # UIで使う値は try の外で初期化しておく（UI構築時の未定義事故を避ける）
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
            notion = NotionClient(cfg.notion_api_key, cfg.notion_db_id)

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
                method="OpenAI",
                reason=result.rationale,
                severity=result.severity,
                categories=result.categories,
                post_link=post_link,
                article_id=result.article_id,
                confidence=result.confidence,
                message_ts=ev["ts"],
            )
            log_info(context, action="notion_page_created", page_id=notion_page_id)

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

        # UI定義（slack_ui.py）に委譲
        # 「確信度はLLMのときのみ」: 現状の run_moderation は method を返さないため、
        # 一旦は rationale 文言から NG 判定を判別する暫定ロジック
        rationale_text = result.rationale or ""
        if cfg.use_mock_openai:
            detection_method = "Mock"
            confidence_for_ui = None
        elif "NGパターン検出" in rationale_text:
            detection_method = "NGワード"
            confidence_for_ui = None
        else:
            detection_method = "LLM"
            confidence_for_ui = result.confidence

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
            # プルダウンは slack_ui.py 側の DEFAULT（選択肢1/2/3）を利用
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
