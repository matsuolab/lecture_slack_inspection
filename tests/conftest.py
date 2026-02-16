# root/tests/conftest.py
import sys
import os
import json
from pathlib import Path
import pytest
from unittest.mock import MagicMock

# -----------------------------
# Paths
# -----------------------------
PROJECT_ROOT = Path(__file__).resolve().parents[1]
LAMBDA_DIR = PROJECT_ROOT / "lambda"
CONTRACTS_DIR = PROJECT_ROOT / "contracts"
FIXTURES_DIR = CONTRACTS_DIR / "fixtures"

# lambdaディレクトリをモジュール検索パスに追加
sys.path.append(str(LAMBDA_DIR))


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# -----------------------------
# Contract / fixtures loader
# -----------------------------
@pytest.fixture(scope="session")
def load_contract_fixture():
    """
    contracts/fixtures/*.json を読み込むローダー
    例: load_contract_fixture("event_api_message.json")
    """
    def _load(name: str) -> dict:
        p = FIXTURES_DIR / name
        if not p.exists():
            raise FileNotFoundError(f"fixture not found: {p}")
        return _read_json(p)
    return _load


@pytest.fixture(scope="session")
def alert_button_value_schema():
    """
    contracts/alert_button_value.schema.json を読み込む
    """
    schema_path = CONTRACTS_DIR / "alert_button_value.schema.json"
    if not schema_path.exists():
        raise FileNotFoundError(f"schema not found: {schema_path}")
    return _read_json(schema_path)


# -----------------------------
# Env / config mocks
# -----------------------------
@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    """テスト実行時に必要な環境変数をセット"""
    monkeypatch.setenv("SLACK_SIGNING_SECRET", "secret")
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("NOTION_API_KEY", "secret_notion")
    monkeypatch.setenv("NOTION_DB_ID", "db_id")
    monkeypatch.setenv("ALERT_PRIVATE_CHANNEL_ID", "C_ADMIN")
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

    mocker.patch("app_inspect.handler.load_config", return_value=mock_conf)
    mocker.patch("app_alert.handler.load_config", return_value=mock_conf)
    return mock_conf


@pytest.fixture
def mock_external_services(mocker):
    """外部サービスのクライアントをまとめてモック化"""

    # 署名検証：デフォルト False（テスト側で True を明示）
    mock_verifier = mocker.MagicMock()
    mock_verifier.is_valid_request.return_value = False

    mock_slack_client = mocker.MagicMock()
    mock_openai_client = mocker.MagicMock()
    mock_notion_client = mocker.MagicMock()

    mocker.patch("app_inspect.handler.SignatureVerifier", return_value=mock_verifier)
    mocker.patch("app_alert.handler.SignatureVerifier", return_value=mock_verifier)

    mocker.patch("app_inspect.handler.WebClient", return_value=mock_slack_client)
    mocker.patch("app_alert.handler.WebClient", return_value=mock_slack_client)

    mocker.patch("app_inspect.handler.OpenAI", return_value=mock_openai_client)

    mocker.patch("app_inspect.handler.NotionClient", return_value=mock_notion_client)
    mocker.patch("app_alert.handler.NotionClient", return_value=mock_notion_client)

    return {
        "slack": mock_slack_client,
        "signature": mock_verifier,
        "openai": mock_openai_client,
        "notion": mock_notion_client,
    }
