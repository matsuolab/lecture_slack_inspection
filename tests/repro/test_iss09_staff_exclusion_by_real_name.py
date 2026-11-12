"""
ISS-09: スタッフの除外を real_name の「松尾研」で行う。users.info が失敗すると投稿ごと判定されない
docs/requirements/issues.md の該当の節: ## 詳細 > ### ISS-09 スタッフの除外

今の動きを固定する特性テスト。不具合を直したら期待値を書き換えること。
"""

import copy
import json
import pytest
from app_inspect.handler import lambda_handler


def test_staff_exclusion_by_real_name_ignores_admin(
    load_contract_fixture, mock_external_services, mock_config, mocker
):
    """users_info の real_name に「松尾研」が含まれる場合、ignored_admin が返され run_moderation は呼ばれないことを確認。"""
    mock_external_services["signature"].is_valid_request.return_value = True
    mock_external_services["slack"].users_info.return_value = {
        "user": {"real_name": "松尾研 テスト"}
    }
    mock_run_moderation = mocker.patch("app_inspect.handler.run_moderation")

    event = copy.deepcopy(load_contract_fixture("event_api_message.json"))
    body = json.loads(event["body"])
    body["event"]["user"] = "U_STAFF"
    event["body"] = json.dumps(body)
    event.setdefault("headers", {})
    event["headers"].update({"x-slack-signature": "dummy", "x-slack-request-timestamp": "123"})

    resp = lambda_handler(event, {})

    assert resp["statusCode"] == 200
    assert resp["body"] == "ignored_admin"
    mock_run_moderation.assert_not_called()


def test_users_info_exception_causes_error_handled_and_skips_moderation(
    load_contract_fixture, mock_external_services, mock_config, mocker
):
    """users_info が例外を投げた場合、error_handled（200）で終了し run_moderation が呼ばれないことを確認。"""
    mock_external_services["signature"].is_valid_request.return_value = True
    mock_external_services["slack"].users_info.side_effect = Exception("Slack API rate limited")
    mock_run_moderation = mocker.patch("app_inspect.handler.run_moderation")

    event = copy.deepcopy(load_contract_fixture("event_api_message.json"))
    body = json.loads(event["body"])
    body["event"]["user"] = "U_USER"
    event["body"] = json.dumps(body)
    event.setdefault("headers", {})
    event["headers"].update({"x-slack-signature": "dummy", "x-slack-request-timestamp": "123"})

    resp = lambda_handler(event, {})

    assert resp["statusCode"] == 200
    assert resp["body"] == "error_handled"
    mock_run_moderation.assert_not_called()
