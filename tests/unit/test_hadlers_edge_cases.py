import json
import urllib.parse
import pytest
from unittest.mock import MagicMock

from app_inspect.handler import lambda_handler as inspect_handler
from app_alert.handler import lambda_handler as alert_handler

# ==========================================
# ユーティリティ関数
# ==========================================
def _create_apigw_event(body_dict: dict, headers: dict = None) -> dict:
    """Slack Event APIからのリクエストを模したAPI Gatewayイベントを作成"""
    return {
        "body": json.dumps(body_dict),
        "headers": headers or {"content-type": "application/json"},
        "isBase64Encoded": False,
    }

def _create_interactivity_event(payload_dict: dict, headers: dict = None) -> dict:
    """Slack Interactivityからのリクエストを模したAPI Gatewayイベントを作成"""
    body_str = "payload=" + urllib.parse.quote(json.dumps(payload_dict))
    base_headers = {"content-type": "application/x-www-form-urlencoded"}
    if headers:
        base_headers.update(headers)
    return {
        "body": body_str,
        "headers": base_headers,
        "isBase64Encoded": False,
    }

# ==========================================
# Lambda A (app_inspect) のエッジケーステスト
# ==========================================

def test_inspect_retry_skip(mock_external_services, mock_config):
    """Slackからの再送イベント (x-slack-retry-num) を即座にスキップすることを確認"""
    event = _create_apigw_event(
        {"type": "event_callback", "event": {"type": "message", "text": "test"}},
        {"x-slack-retry-num": "1", "content-type": "application/json"}
    )
    resp = inspect_handler(event, {})
    
    assert resp["statusCode"] == 200
    assert resp["body"] == "ok"
    # OpenAIなどの外部APIが一切呼ばれていないことを確認
    mock_external_services["openai"].chat.completions.create.assert_not_called()

def test_inspect_url_verification(mock_external_services, mock_config):
    """Slack Appの初期設定時の url_verification イベントに対して challenge を返すことを確認"""
    event = _create_apigw_event({
        "type": "url_verification",
        "challenge": "test_challenge_string"
    })
    resp = inspect_handler(event, {})
    
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["challenge"] == "test_challenge_string"

def test_inspect_invalid_signature(mock_external_services, mock_config):
    """署名検証に失敗した場合、401を返すことを確認"""
    # モックで署名検証を明示的にFalseにする
    mock_external_services["signature"].is_valid_request.return_value = False
    event = _create_apigw_event(
        {"type": "event_callback", "event": {"type": "message", "text": "test"}}
    )
    resp = inspect_handler(event, {})
    
    assert resp["statusCode"] == 401
    assert resp["body"] == "invalid signature"

def test_inspect_not_violation(mock_external_services, mock_config, mocker):
    """判定結果が「違反なし」の場合、Slack/Notion連携されずに終了することを確認"""
    mock_external_services["signature"].is_valid_request.return_value = True
    
    # 違反なし (is_violation=False) のモデレーション結果をモック
    mock_result = MagicMock()
    mock_result.is_violation = False
    mock_result.severity = "low"
    mocker.patch("app_inspect.handler.run_moderation", return_value=mock_result)

    event = _create_apigw_event({
        "type": "event_callback",
        "event": {"type": "message", "text": "safe text", "channel": "C1", "ts": "123"}
    })
    resp = inspect_handler(event, {})
    
    assert resp["statusCode"] == 200
    assert resp["body"] == "ok"
    # Notionへのログ記録やSlack通知が呼ばれないことを確認
    mock_external_services["slack"].chat_postMessage.assert_not_called()
    mock_external_services["notion"].create_violation_log.assert_not_called()

def test_inspect_duplicate_violation(mock_external_services, mock_config, mocker):
    """重複チェックで既存違反と判定された場合、処理がスキップされることを確認"""
    mock_external_services["signature"].is_valid_request.return_value = True
    
    mock_result = MagicMock()
    mock_result.is_violation = True
    mock_result.severity = "high"
    mocker.patch("app_inspect.handler.run_moderation", return_value=mock_result)
    
    # すでにNotionに登録されている（重複している）状態をモック
    mock_external_services["notion"].check_duplicate_violation.return_value = True

    event = _create_apigw_event({
        "type": "event_callback", "event": {"type": "message", "text": "spam text", "channel": "C1", "ts": "123"}
    })
    resp = inspect_handler(event, {})
    
    assert resp["statusCode"] == 200
    assert resp["body"] == "duplicate"
    # 登録・通知処理がスキップされることを確認
    mock_external_services["slack"].chat_postMessage.assert_not_called()
    mock_external_services["notion"].create_violation_log.assert_not_called()

def test_inspect_external_api_error(mock_external_services, mock_config, mocker):
    """Slack API等で例外が発生しても、ハンドラがクラッシュせず200を返す（Slackの再送ループを防ぐ）ことを確認"""
    mock_external_services["signature"].is_valid_request.return_value = True
    
    mock_result = MagicMock()
    mock_result.is_violation = True
    mock_result.severity = "high"
    mocker.patch("app_inspect.handler.run_moderation", return_value=mock_result)
    
    # Slackへの通知処理で例外を発生させる
    mock_external_services["slack"].chat_postMessage.side_effect = Exception("Slack API Error")

    event = _create_apigw_event({
        "type": "event_callback", "event": {"type": "message", "text": "spam text", "channel": "C1", "ts": "123"}
    })
    resp = inspect_handler(event, {})
    
    assert resp["statusCode"] == 200
    assert resp["body"] == "error_handled"


# ==========================================
# Lambda B (app_alert) のエッジケーステスト
# ==========================================

def test_alert_invalid_signature(mock_external_services, mock_config):
    """署名検証に失敗した場合、401を返すことを確認"""
    mock_external_services["signature"].is_valid_request.return_value = False
    event = _create_interactivity_event({"type": "block_actions"})
    resp = alert_handler(event, {})
    
    assert resp["statusCode"] == 401
    assert resp["body"] == "Invalid signature"

def test_alert_ignore_unknown_action(mock_external_services, mock_config):
    """想定外のボタン(action_id)が押された場合は無視して200を返すことを確認"""
    mock_external_services["signature"].is_valid_request.return_value = True
    event = _create_interactivity_event({
        "type": "block_actions",
        "actions": [{"action_id": "unknown_action", "value": "{}"}]
    })
    resp = alert_handler(event, {})
    
    assert resp["statusCode"] == 200
    assert resp["body"] == "OK"
    mock_external_services["notion"].update_status.assert_not_called()

def test_alert_external_api_error(mock_external_services, mock_config):
    """Slack連携などでエラーが発生した場合、システムダウンせず Action Failed を返すことを確認"""
    mock_external_services["signature"].is_valid_request.return_value = True
    
    # Slackへの返信でエラーが発生する設定
    mock_external_services["slack"].chat_postMessage.side_effect = Exception("Slack API Error")

    event = _create_interactivity_event({
        "type": "block_actions",
        "actions": [{
            "action_id": "approve_violation",
            "value": json.dumps({"origin_channel": "C1", "origin_ts": "123", "notion_page_id": "P1"})
        }]
    })
    resp = alert_handler(event, {})
    
    assert resp["statusCode"] == 200
    assert resp["body"] == "Action Failed"