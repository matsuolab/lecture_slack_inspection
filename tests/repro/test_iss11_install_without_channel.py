"""ISS-11 channel を付けずにインストールすると、そのワークスペースの投稿がすべて判定されない

docs/requirements/issues.md: ISS-11 alert_channel_id が無いワークスペース
今の動きを固定する特性テスト。不具合を直したら期待値を書き換えること。
"""

import pytest

from app_oauth.handler import _save_installation
from app_inspect.services.config import load_config


def test_save_installation_without_channel_does_not_save_alert_channel_id(monkeypatch):
    """alert_channel_id が None の場合、bot_token と installed_at の 2 回だけ保存され alert_channel_id が保存されないことを確かめる。"""
    put_calls = []
    monkeypatch.setattr("app_oauth.handler.put_secure_parameter", lambda name, value: put_calls.append((name, value)))
    monkeypatch.setattr("app_oauth.handler.log_info", lambda *args, **kwargs: None)

    _save_installation(
        context={},
        prefix="/slack/installation",
        team_id="T_TEST",
        access_token="xoxb-dummy",
        alert_channel_id=None,
    )

    assert len(put_calls) == 2
    param_names = [call[0] for call in put_calls]
    assert "/slack/installation/T_TEST/bot_token" in param_names
    assert "/slack/installation/T_TEST/installed_at" in param_names
    assert not any("alert_channel_id" in name for name in param_names)


def test_inspect_load_config_raises_runtime_error_when_alert_channel_id_missing(mocker):
    """bot_token はあるが alert_channel_id が存在しない場合、load_config が RuntimeError を投げることを確かめる。"""
    def mock_get_parameter(name):
        if name.endswith("/bot_token"):
            return "xoxb-dummy"
        if name.endswith("/alert_channel_id"):
            return None
        return None

    mocker.patch("app_inspect.services.config.get_parameter_by_name", side_effect=mock_get_parameter)
    mocker.patch("app_inspect.services.config.get_secret", return_value="dummy-secret")

    with pytest.raises(RuntimeError) as exc_info:
        load_config("T_TEST")

    assert "Missing alert channel ID for team T_TEST." in str(exc_info.value)
