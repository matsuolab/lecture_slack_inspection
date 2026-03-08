# tests/unit/test_app_oauth.py

import urllib.parse
from unittest.mock import MagicMock

import pytest

import app_oauth.handler as oauth


class DummyResponse:
    """requests.post の戻り値を簡易的に再現するダミー"""

    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


@pytest.fixture
def oauth_common_mocks(monkeypatch):
    """
    app_oauth.handler の外部依存を最小限モックする。
    - Parameter Store 保存
    - observability
    - OAuth 関連設定
    """
    put_calls = []

    secret_map = {
        "SLACK_CLIENT_ID_PARAM_NAME": "111.222",
        "SLACK_CLIENT_SECRET_PARAM_NAME": "super-secret",
    }

    monkeypatch.setattr(oauth, "build_context", lambda *args, **kwargs: {})
    monkeypatch.setattr(oauth, "log_info", lambda *args, **kwargs: None)
    monkeypatch.setattr(oauth, "log_error", lambda *args, **kwargs: None)

    # ここを 0引数固定ではなく可変引数にする
    monkeypatch.setattr(
        oauth,
        "_oauth_redirect_uri",
        lambda *args, **kwargs: "https://example.com/slack/oauth/callback",
    )
    monkeypatch.setattr(
        oauth,
        "_bot_scopes",
        lambda *args, **kwargs: "incoming-webhook,team:read",
    )

    monkeypatch.setattr(
        oauth,
        "get_secret",
        lambda name, *args, **kwargs: secret_map.get(name, ""),
    )
    monkeypatch.setattr(
        oauth,
        "put_secure_parameter",
        lambda name, value, *args, **kwargs: put_calls.append((name, value)),
    )

    monkeypatch.setenv("SLACK_INSTALLATION_PARAM_PREFIX", "/slack/installation")

    return {
        "put_calls": put_calls,
    }


def test_state_sign_and_verify_round_trip(monkeypatch):
    """署名した state を検証すると元の payload に戻ること"""
    monkeypatch.setattr(oauth, "_oauth_state_secret", lambda: "state-secret")
    monkeypatch.setattr(oauth.time, "time", lambda: 1_700_000_000)

    payload = {
        "iat": 1_700_000_000,
        "nonce": "abc123",
        "team": "T123",
    }

    state = oauth._sign_state(payload)

    assert oauth._verify_state(state, max_age_seconds=600) == payload


def test_allowed_team_ids_accepts_json_array(monkeypatch):
    """allow-list が JSON 配列形式でも正しく読めること"""
    monkeypatch.setattr(
        oauth,
        "get_secret_no_cache",
        lambda name: '["T111", " T222 ", ""]',
    )

    assert oauth._allowed_team_ids() == {"T111", "T222"}


def test_allowed_team_ids_accepts_csv(monkeypatch):
    """allow-list がカンマ区切り形式でも正しく読めること"""
    monkeypatch.setattr(
        oauth,
        "get_secret_no_cache",
        lambda name: "T111, T222 ,",
    )

    assert oauth._allowed_team_ids() == {"T111", "T222"}


def test_handle_start_rejects_non_allowed_team(monkeypatch, oauth_common_mocks):
    """許可されていない team 指定で /start が 403 を返すこと"""
    monkeypatch.setattr(oauth, "_allowed_team_ids", lambda: {"T111"})

    event = {
        "queryStringParameters": {
            "team": "T999",
        }
    }

    resp = oauth._handle_start(event, {})

    assert resp["statusCode"] == 403
    assert "Installation is not allowed for this workspace" in resp["body"]


def test_handle_start_redirects_to_slack_authorize(monkeypatch, oauth_common_mocks):
    """許可された team 指定で Slack authorize URL に redirect すること"""
    monkeypatch.setattr(oauth, "_allowed_team_ids", lambda: {"T123"})
    monkeypatch.setattr(oauth, "_sign_state", lambda payload: "signed-state")

    event = {
        "queryStringParameters": {
            "team": "T123",
        }
    }

    resp = oauth._handle_start(event, {})

    assert resp["statusCode"] == 302

    location = resp["headers"]["Location"]
    parsed = urllib.parse.urlparse(location)
    query = urllib.parse.parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "slack.com"
    assert parsed.path == "/oauth/v2/authorize"

    assert query["client_id"] == ["111.222"]
    assert query["scope"] == ["incoming-webhook,team:read"]
    assert query["redirect_uri"] == ["https://example.com/slack/oauth/callback"]
    assert query["state"] == ["signed-state"]
    assert query["team"] == ["T123"]


def test_handle_callback_requires_state(monkeypatch, oauth_common_mocks):
    """callback で state が無い場合は 400 を返すこと"""
    event = {
        "queryStringParameters": {
            "code": "valid-code",
        }
    }

    resp = oauth._handle_callback(event, {})

    assert resp["statusCode"] == 400
    assert "Missing state parameter" in resp["body"]


def test_handle_callback_rejects_not_allowed_team_and_revokes_token(
    monkeypatch, oauth_common_mocks
):
    """
    allow-list に無い workspace なら 403 を返し、
    発行された token を revoke すること。
    以前のログ出力起因の例外の再発防止も兼ねる。
    """
    revoke_mock = MagicMock()

    monkeypatch.setattr(oauth, "_verify_state", lambda state: {"team": None})
    monkeypatch.setattr(oauth, "_allowed_team_ids", lambda: {"T_ALLOWED"})
    monkeypatch.setattr(oauth, "_revoke_token", revoke_mock)
    monkeypatch.setattr(
        oauth.requests,
        "post",
        lambda *args, **kwargs: DummyResponse(
            {
                "ok": True,
                "team": {"id": "T_OTHER"},
                "access_token": "xoxb-denied",
                "incoming_webhook": {"channel_id": "C_ALERT"},
            }
        ),
    )

    event = {
        "queryStringParameters": {
            "code": "valid-code",
            "state": "signed-state",
        }
    }

    resp = oauth._handle_callback(event, {})

    assert resp["statusCode"] == 403
    assert "Installation is not allowed for this workspace" in resp["body"]
    revoke_mock.assert_called_once_with("xoxb-denied")
    assert oauth_common_mocks["put_calls"] == []


def test_handle_callback_rejects_team_mismatch_and_revokes_token(
    monkeypatch, oauth_common_mocks
):
    """state に入れた期待 team と実際の team が違う場合は 403 + revoke すること"""
    revoke_mock = MagicMock()

    monkeypatch.setattr(oauth, "_verify_state", lambda state: {"team": "T_EXPECT"})
    monkeypatch.setattr(oauth, "_allowed_team_ids", lambda: {"T_EXPECT", "T_OTHER"})
    monkeypatch.setattr(oauth, "_revoke_token", revoke_mock)
    monkeypatch.setattr(
        oauth.requests,
        "post",
        lambda *args, **kwargs: DummyResponse(
            {
                "ok": True,
                "team": {"id": "T_OTHER"},
                "access_token": "xoxb-mismatch",
                "incoming_webhook": {"channel_id": "C_ALERT"},
            }
        ),
    )

    event = {
        "queryStringParameters": {
            "code": "valid-code",
            "state": "signed-state",
        }
    }

    resp = oauth._handle_callback(event, {})

    assert resp["statusCode"] == 403
    assert "OAuth Workspace mismatch" in resp["body"]
    revoke_mock.assert_called_once_with("xoxb-mismatch")
    assert oauth_common_mocks["put_calls"] == []


def test_handle_callback_success_saves_team_specific_parameters(
    monkeypatch, oauth_common_mocks
):
    """正常な callback では team ごとの bot token / channel / installed_at を保存すること"""
    monkeypatch.setattr(oauth, "_verify_state", lambda state: {"team": "T123"})
    monkeypatch.setattr(oauth, "_allowed_team_ids", lambda: {"T123"})
    monkeypatch.setattr(oauth.time, "time", lambda: 1_700_000_000)
    monkeypatch.setattr(
        oauth.requests,
        "post",
        lambda *args, **kwargs: DummyResponse(
            {
                "ok": True,
                "team": {"id": "T123"},
                "access_token": "xoxb-installed",
                "incoming_webhook": {"channel_id": "C_ALERT"},
            }
        ),
    )

    event = {
        "queryStringParameters": {
            "code": "valid-code",
            "state": "signed-state",
        }
    }

    resp = oauth._handle_callback(event, {})

    assert resp["statusCode"] == 200
    assert "team_id: T123" in resp["body"]
    assert "alert_channel_id: C_ALERT" in resp["body"]

    assert oauth_common_mocks["put_calls"] == [
        ("/slack/installation/T123/bot_token", "xoxb-installed"),
        ("/slack/installation/T123/alert_channel_id", "C_ALERT"),
        ("/slack/installation/T123/installed_at", "1700000000"),
    ]