import json
import pytest
from unittest.mock import MagicMock

from app_inspect.handler import lambda_handler as lambdaA
from app_alert.handler import lambda_handler as lambdaB

jsonschema = pytest.importorskip("jsonschema")


def _find_button_by_action_id(blocks: list, action_id: str) -> dict:
    for b in blocks:
        if b.get("type") == "actions":
            for el in b.get("elements", []):
                if el.get("type") == "button" and el.get("action_id") == action_id:
                    return el
    raise AssertionError(f"button not found: action_id={action_id}")


def test_A_to_B_flow_dismiss_updates_notion(
    load_contract_fixture, alert_button_value_schema, mock_external_services, mock_config, mocker, to_apigw_form_event
):
    # 共通：署名はユニットなのでOK扱い
    mock_external_services["signature"].is_valid_request.return_value = True

    mock_slack = mock_external_services["slack"]
    mock_notion = mock_external_services["notion"]

    # --- A側の前提（違反ありで通知を出す）
    mock_result = MagicMock()
    mock_result.is_violation = True
    mock_result.severity = "high"
    mock_result.rationale = "spam"
    mock_result.confidence = 0.9
    mock_result.article_id = "A-123"
    mocker.patch("app_inspect.handler.run_moderation", return_value=mock_result)

    mock_slack.chat_getPermalink.return_value = {"permalink": "http://slack.com/p1"}
    mock_notion.create_violation_log.return_value = "page-id-123"

    eventA = load_contract_fixture("event_api_message.json")
    eventA.setdefault("headers", {})
    eventA["headers"].update({"x-slack-signature": "dummy", "x-slack-request-timestamp": "123"})

    # 1) A handler 実行
    respA = lambdaA(eventA, {})
    assert respA["statusCode"] == 200

    # 2) Aが送った blocks から dismiss ボタンを抽出 → value_from_A を作る
    mock_slack.chat_postMessage.assert_called_once()
    _, kwargsA = mock_slack.chat_postMessage.call_args
    blocks = kwargsA["blocks"]

    dismiss_btn = _find_button_by_action_id(blocks, "dismiss_violation")
    value_from_A = json.loads(dismiss_btn["value"])

    # スキーマにも適合していること（契約担保）
    jsonschema.validate(instance=value_from_A, schema=alert_button_value_schema)

    # --- B側へ接続：Aが生成した value をそのまま actions[0].value に入れる
    mock_slack.reset_mock()
    mock_notion.reset_mock()

    payloadB = load_contract_fixture("interactivity_button_click_dismiss.json")
    payloadB["actions"][0]["value"] = json.dumps(value_from_A)

    # 実装で container を参照する場合に備えて補完
    payloadB.setdefault("container", {})
    payloadB["container"]["channel_id"] = "C_PRIVATE"
    payloadB["container"]["message_ts"] = "1700000001.00001"

    eventB = to_apigw_form_event(payloadB)

    # 3) B handler 実行
    respB = lambdaB(eventB, {})
    assert respB["statusCode"] == 200

    # 4) dismiss では origin 返信しない（設計どおりなら）
    mock_slack.chat_postMessage.assert_not_called()

    # 5) Notion status を dismiss に更新する
    mock_notion.update_status.assert_called_once()
    call_args = mock_notion.update_status.call_args
    assert call_args.args[0] == value_from_A["notion_page_id"]
    assert call_args.args[1] == "Dismissed"
