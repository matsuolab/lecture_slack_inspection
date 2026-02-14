import json
import pytest
from app_inspect.handler import lambda_handler

class TestAppInspect:
    def test_url_verification(self, mock_env, mock_config):
        """SlackのURL検証リクエストに正しく応答できるか"""
        event = {
            "body": json.dumps({"type": "url_verification", "challenge": "test_challenge"}),
            "headers": {},
            "isBase64Encoded": False
        }
        resp = lambda_handler(event, {})
        assert resp["statusCode"] == 200
        assert json.loads(resp["body"])["challenge"] == "test_challenge"

    def test_ignore_bot_message(self, mock_env, mock_external_services, mock_config):
        """Bot自身のメッセージは無視するか"""
        mock_verifier = mock_external_services["signature"].return_value
        mock_verifier.is_valid_request.return_value = True

        event_body = {
            "type": "event_callback",
            "event": {
                "type": "message",
                "bot_id": "B12345", # Botからのメッセージ
                "text": "Hello"
            }
        }
        event = {
            "body": json.dumps(event_body),
            "headers": {"x-slack-signature": "s", "x-slack-request-timestamp": "t"}
        }
        
        resp = lambda_handler(event, {})
        assert resp["body"] == "ignored"

    def test_violation_detected(self, mock_env, mock_external_services, mock_config, mocker):
        """違反を検知した際、Notion作成とSlack通知が行われるか"""
        # 署名検証OK
        mock_external_services["signature"].return_value.is_valid_request.return_value = True
        
        # モデレーション結果をモック (違反あり)
        mock_result = MagicMock()
        mock_result.is_violation = True
        mock_result.severity = "high"
        mock_result.rationale = "不適切な発言"
        mocker.patch("app_inspect.handler.run_moderation", return_value=mock_result)

        # Slackパーマリンク取得のモック
        mock_slack = mock_external_services["slack"].return_value
        mock_slack.chat_getPermalink.return_value = {"permalink": "http://slack.com/p1"}

        # Notion作成のモック
        mock_notion = mock_external_services["notion"].return_value
        mock_notion.create_violation_log.return_value = "page-id-123"

        event_body = {
            "type": "event_callback",
            "event": {
                "type": "message",
                "text": "死ね",
                "user": "U12345",
                "channel": "C12345",
                "ts": "123456.789"
            }
        }
        event = {
            "body": json.dumps(event_body),
            "headers": {"x-slack-signature": "s", "x-slack-request-timestamp": "t"}
        }

        resp = lambda_handler(event, {})

        assert resp["statusCode"] == 200
        # Notionにログが作成されたか確認
        mock_notion.create_violation_log.assert_called_once()
        # 管理者チャンネルにアラートが飛んだか確認
        mock_slack.chat_postMessage.assert_called_once()
        args, kwargs = mock_slack.chat_postMessage.call_args
        assert kwargs["channel"] == "C_ADMIN"
        assert "削除勧告を送る" in str(kwargs["blocks"])

    def test_no_violation(self, mock_env, mock_external_services, mock_config, mocker):
        """違反がない場合、何もしないか"""
        mock_external_services["signature"].return_value.is_valid_request.return_value = True
        
        mock_result = MagicMock()
        mock_result.is_violation = False
        mocker.patch("app_inspect.handler.run_moderation", return_value=mock_result)

        event_body = {
            "type": "event_callback",
            "event": {"type": "message", "text": "こんにちは"}
        }
        event = {"body": json.dumps(event_body), "headers": {}}

        resp = lambda_handler(event, {})
        assert resp["statusCode"] == 200
        assert resp["body"] == "ok"
        
        # 外部呼び出しが行われていないこと
        mock_external_services["notion"].return_value.create_violation_log.assert_not_called()