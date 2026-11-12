"""ISS-07 app_batch は移行後も旧方式（gpt-4o-mini）のまま。廃止後は全件「違反なし」

docs/requirements/issues.md: ISS-07 app_batch は旧方式のまま
今の動きを固定する特性テスト。不具合を直したら期待値を書き換えること。
"""

from unittest.mock import MagicMock

from app_batch.services.config import load_config
from app_inspect.services.violation_detector import ViolationDetector


def test_batch_config_defaults_to_gpt_4o_mini_and_resolves_to_llm_score(mocker, monkeypatch):
    """OPENAI_MODEL が未設定のとき、app_batch の設定が gpt-4o-mini となり ViolationDetector が llm_score 方式になることを確かめる。"""
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.setenv("DETECTION_MODE", "auto")

    mocker.patch("app_batch.services.config.get_parameter_by_name", return_value="xoxb-dummy")
    mocker.patch("app_batch.services.config.get_secret", return_value="sk-dummy")

    cfg = load_config("T_TEST")
    assert cfg.openai_model == "gpt-4o-mini"

    detector = ViolationDetector(
        openai_client=MagicMock(),
        judge_model=cfg.openai_model,
        detection_mode="auto",
    )
    assert detector.detection_mode == "llm_score"
