"""
ISS-32: Slack の送り直しを、理由（x-slack-retry-reason）を見ずにすべて捨てる
docs/requirements/issues.md の該当の節: ## 詳細 > ### ISS-32 送り直しを理由を見ずに捨てる

今の動きを固定する特性テスト。不具合を直したら期待値を書き換えること。
"""

import copy
import pytest
from app_inspect.handler import lambda_handler


@pytest.mark.parametrize("retry_reason", ["http_timeout", "connection_failed", "http_error"])
def test_retry_discarded_regardless_of_reason(
    retry_reason, load_contract_fixture, mock_external_services, mock_config, mocker
):
    """x-slack-retry-num ヘッダーが存在する場合、x-slack-retry-reason に関係なく署名検証も判定処理も呼ばれずに 200 で終了することを確認。"""
    mock_run_moderation = mocker.patch("app_inspect.handler.run_moderation")

    event = copy.deepcopy(load_contract_fixture("event_api_message.json"))
    event["headers"] = {
        "content-type": "application/json",
        "x-slack-retry-num": "1",
        "x-slack-retry-reason": retry_reason,
    }

    resp = lambda_handler(event, {})

    assert resp["statusCode"] == 200
    assert resp["body"] == "ok"
    mock_external_services["signature"].is_valid_request.assert_not_called()
    mock_run_moderation.assert_not_called()
