import sys
import os
import pytest
from unittest.mock import MagicMock

# lambdaディレクトリをモジュール検索パスに追加
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../lambda")))

@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    """テスト実行時に必要な環境変数をセット"""
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "secret")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("NOTION_API_KEY", "secret_notion")
    monkeypatch.setenv("NOTION_DB_ID", "db_id")
    # 設定ファイル読み込み時のデフォルト値を固定
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4")
    monkeypatch.setenv("GUIDELINES_TEXT", "Spam is prohibited.")

@pytest.fixture
def mock_config(mocker):
    """Configオブジェクトのモック"""
    mock_conf = MagicMock()
    mock_conf.slack_signing_secret = "secret"
    mock_conf.slack_bot_token = "xoxb-test"
    mock_conf.openai_api_key = "sk-test"
    mock_conf.openai_model = "gpt-4"
    mock_conf.guidelines_text = "No spam"
    mock_conf.notion_api_key = "secret_notion"
    mock_conf.notion_db_id = "db_id"
    mock_conf.min_severity_to_alert = "medium"
    mock_conf.use_mock_openai = False
    mock_conf.alert_private_channel_id = "C_ADMIN"
    mock_conf.reply_prefix = "警告:"
    
    # どちらのLambdaのload_configもパッチする
    mocker.patch("app_inspect.services.config.load_config", return_value=mock_conf)
    mocker.patch("app_alert.services.config.load_config", return_value=mock_conf)
    return mock_conf

@pytest.fixture
def mock_external_services(mocker):
    """外部サービスのクライアントをまとめてモック化"""
    return {
        "slack": mocker.patch("slack_sdk.WebClient"),
        "signature": mocker.patch("slack_sdk.signature.SignatureVerifier"),
        "openai": mocker.patch("openai.OpenAI"),
        "notion": mocker.patch("common.notion_client.NotionClient"),
    }