import json
from unittest.mock import MagicMock
import pytest
from app_inspect.handler import lambda_handler

jsonschema = pytest.importorskip("jsonschema")


def _find_first_button(blocks: list) -> dict:
    """
    Slack blocks から最初の button element を返す
    """
    for b in blocks:
        if b.get("type") == "actions":
            for el in b.get("elements", []):
                if el.get("type") == "button":
                    return el
    raise AssertionError("No button element found in blocks")


def test_lambdaA_emits_contract_compliant_button_value(
    load_contract_fixture, alert_button_value_schema, mock_external_services, mock_config, mocker
):
    # 署名OK（このテストでは署名そのものは目的ではない）
    mock_external_services["signature"].is_valid_request.return_value = True

    # moderation: 違反あり
    mock_result = MagicMock()
    mock_result.is_violation = True
    mock_result.severity = "high"
    mock_result.rationale = "spam"
    mocker.patch("app_inspect.handler.run_moderation", return_value=mock_result)

    # Slack/Notionの戻り値
    mock_slack = mock_external_services["slack"]
    mock_slack.chat_getPermalink.return_value = {"permalink": "http://slack.com/p1"}
    mock_notion = mock_external_services["notion"]
    mock_notion.create_violation_log.return_value = "page-id-123"

    # contracts/fixtures の Event API 入力をそのまま使う
    event = load_contract_fixture("event_api_message.json")
    # 実装がヘッダ参照する可能性に備えて最低限入れる（本番寄り）
    event.setdefault("headers", {})
    event["headers"].update({"x-slack-signature": "dummy", "x-slack-request-timestamp": "123"})

    resp = lambda_handler(event, {})
    assert resp["statusCode"] == 200

    # Slackへ private通知したか
    mock_slack.chat_postMessage.assert_called_once()
    _, kwargs = mock_slack.chat_postMessage.call_args
    blocks = kwargs["blocks"]

    # ボタン契約を検証
    btn = _find_first_button(blocks)
    assert btn["action_id"] == "approve_violation"

    value = json.loads(btn["value"])
    jsonschema.validate(instance=value, schema=alert_button_value_schema)

    # 入力イベントと整合することを検証（ここでA-B間の壊れを止める）
    body = json.loads(event["body"])
    ev = body["event"]

    assert value["origin_channel"] == ev["channel"]
    assert value["origin_ts"] == ev["ts"]
    assert value["trace_id"] == f"slack:{body['event_id']}"
