import json
import urllib.parse
from app_alert.handler import lambda_handler

class TestAppAlert:
    def test_approve_violation_action(self, mock_env, mock_external_services, mock_config):
        """管理者が『削除勧告』ボタンを押した時の動作確認"""
        mock_external_services["signature"].return_value.is_valid_request.return_value = True
        
        mock_slack = mock_external_services["slack"].return_value
        mock_notion = mock_external_services["notion"].return_value

        # ボタンのvalueに埋め込まれているデータ
        action_value = {
            "origin_channel": "C_USER",
            "origin_ts": "111.222",
            "notion_page_id": "page-123",
            "trace_id": "trace-abc"
        }
        
        # Slackからのpayload (x-www-form-urlencoded)
        payload = {
            "type": "block_actions",
            "actions": [
                {"action_id": "approve_violation", "value": json.dumps(action_value)}
            ],
            "container": {"channel_id": "C_ADMIN", "message_ts": "999.888"}
        }
        body_str = "payload=" + urllib.parse.quote(json.dumps(payload))
        
        event = {
            "body": body_str,
            "headers": {"content-type": "application/x-www-form-urlencoded"},
            "isBase64Encoded": False
        }

        resp = lambda_handler(event, {})

        assert resp["statusCode"] == 200
        
        # 1. 元の投稿者（ユーザー）のスレッドに警告メッセージを送ったか
        mock_slack.chat_postMessage.assert_called_once()
        _, kwargs = mock_slack.chat_postMessage.call_args
        assert kwargs["channel"] == "C_USER"
        assert kwargs["thread_ts"] == "111.222"
        
        # 2. Notionのステータスを更新したか
        mock_notion.update_status.assert_called_with("page-123", "対応済み")
        
        # 3. 管理者画面のボタンを「対応済み」に書き換えたか
        mock_slack.chat_update.assert_called_once()
        _, kwargs = mock_slack.chat_update.call_args
        assert kwargs["text"] == "対応済み"