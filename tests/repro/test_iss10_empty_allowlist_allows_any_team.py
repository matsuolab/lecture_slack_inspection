"""ISS-10 許可リストが空だと、どのワークスペースでもインストールできる

docs/requirements/issues.md: ISS-10 許可リストが空なら誰でもインストールできる
今の動きを固定する特性テスト。不具合を直したら期待値を書き換えること。
"""

import time
from unittest.mock import MagicMock

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


def test_handle_start_redirects_when_allowlist_is_empty(monkeypatch):
    """許可リストが空のとき、未知の team 指定でも 302 リダイレクトで認可画面へ遷移することを確かめる。"""
    monkeypatch.setattr(oauth, "build_context", lambda *args, **kwargs: {})
    monkeypatch.setattr(oauth, "log_info", lambda *args, **kwargs: None)
    monkeypatch.setattr(oauth, "log_error", lambda *args, **kwargs: None)
    monkeypatch.setattr(oauth, "_allowed_team_ids", lambda: set())
    monkeypatch.setattr(oauth, "_oauth_state_secret", lambda: "state-secret")
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
    monkeypatch.setattr(oauth, "get_secret", lambda name, *args, **kwargs: "111.222")

    event = {
        "queryStringParameters": {
            "team": "T_UNKNOWN",
        }
    }

    resp = oauth._handle_start(event, {})
    assert resp["statusCode"] == 302
    assert "https://slack.com/oauth/v2/authorize" in resp["headers"]["Location"]


def test_handle_callback_installs_unknown_team_when_allowlist_is_empty(monkeypatch):
    """許可リストが空のとき、未知の team に対しても 200 でインストールが保存されトークンが失効されないことを確かめる。"""
    put_calls = []
    revoke_mock = MagicMock()

    monkeypatch.setattr(oauth, "build_context", lambda *args, **kwargs: {})
    monkeypatch.setattr(oauth, "log_info", lambda *args, **kwargs: None)
    monkeypatch.setattr(oauth, "log_error", lambda *args, **kwargs: None)
    monkeypatch.setattr(oauth, "_allowed_team_ids", lambda: set())
    monkeypatch.setattr(oauth, "_oauth_state_secret", lambda: "state-secret")
    monkeypatch.setattr(oauth, "_revoke_token", revoke_mock)
    monkeypatch.setattr(oauth, "_send_test_notification", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        oauth, "get_secret", lambda name, *args, **kwargs: "dummy-secret"
    )
    monkeypatch.setattr(
        oauth,
        "put_secure_parameter",
        lambda name, value, *args, **kwargs: put_calls.append((name, value)),
    )
    monkeypatch.setenv("SLACK_INSTALLATION_PARAM_PREFIX", "/slack/installation")

    # 有効な state を作成
    state = oauth._sign_state({
        "iat": int(time.time()),
        "nonce": "test-nonce",
        "team": None,
        "channel": None,
        "redirect_uri": "https://example.com/slack/oauth/callback",
    })

    monkeypatch.setattr(
        oauth.requests,
        "post",
        lambda *args, **kwargs: DummyResponse({
            "ok": True,
            "team": {"id": "T_UNKNOWN"},
            "access_token": "xoxb-unknown",
        }),
    )

    event = {
        "queryStringParameters": {
            "code": "test-code",
            "state": state,
        }
    }

    resp = oauth._handle_callback(event, {})
    assert resp["statusCode"] == 200
    revoke_mock.assert_not_called()
    assert any(k == "/slack/installation/T_UNKNOWN/bot_token" and v == "xoxb-unknown" for k, v in put_calls)
