"""
ISS-26: 編集イベントではスタッフの除外が効かない
docs/requirements/issues.md の該当の節: ## 詳細 > ### ISS-26 編集イベントではスタッフの除外が効かない

今の動きを固定する特性テスト。不具合を直したら期待値を書き換えること。
"""

import copy
import json
import pytest
from app_inspect.handler import lambda_handler


def test_edit_event_skips_staff_exclusion_and_proceeds_to_handle_edited(
    load_contract_fixture, mock_external_services, mock_config, mocker
):
    """message_changed イベントでは event 直下に user が無いため、スタッフであっても users_info が呼ばれずに handle_edited_message に進むことを確認。"""
    mock_external_services["signature"].is_valid_request.return_value = True
    mock_slack = mock_external_services["slack"]
    mock_slack.users_info.return_value = {"user": {"real_name": "松尾研 テスト"}}

    mock_handle_edited = mocker.patch(
        "app_inspect.handler.handle_edited_message",
        return_value={"statusCode": 200, "body": "edited_handled"},
    )

    event = copy.deepcopy(load_contract_fixture("event_api_message.json"))
    body = json.loads(event["body"])
    body["event"] = {
        "type": "message",
        "subtype": "message_changed",
        "hidden": True,
        "channel": "C_TEST",
        "message": {
            "type": "message",
            "user": "U_TEST",
            "text": "編集後の投稿本文",
            "ts": "1700000000.000100",
            "edited": {
                "user": "U_TEST",
                "ts": "1700000010.000200",
            },
        },
        "previous_message": {
            "type": "message",
            "user": "U_TEST",
            "text": "編集前の投稿本文",
            "ts": "1700000000.000100",
        },
        "event_ts": "1700000010.000200",
    }
    # event 直下に user が存在しないことを確認
    assert "user" not in body["event"]

    event["body"] = json.dumps(body)
    event.setdefault("headers", {})
    event["headers"].update({"x-slack-signature": "dummy", "x-slack-request-timestamp": "123"})

    resp = lambda_handler(event, {})

    assert resp["statusCode"] == 200
    assert resp["body"] == "edited_handled"
    mock_slack.users_info.assert_not_called()
    mock_handle_edited.assert_called_once()
