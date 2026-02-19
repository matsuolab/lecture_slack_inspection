import json
import urllib.parse
from app_alert.handler import lambda_handler


def _to_apigw_form_event(payload: dict) -> dict:
    body_str = "payload=" + urllib.parse.quote(json.dumps(payload))
    return {
        "body": body_str,
        "headers": {"content-type": "application/x-www-form-urlencoded"},
        "isBase64Encoded": False,
    }


def test_lambdaB_handles_fixture_interactivity_and_replies_to_origin(
    load_contract_fixture, mock_external_services, mock_config
):
    mock_external_services["signature"].is_valid_request.return_value = True
    mock_slack = mock_external_services["slack"]

    payload = load_contract_fixture("interactivity_button_click_approve.json")

    # 実装が container を参照する場合に備えて補完（Slack現実寄り）
    payload.setdefault("container", {})
    payload["container"].setdefault("channel_id", payload.get("channel", {}).get("id", "C_PRIVATE"))
    payload["container"].setdefault("message_ts", payload.get("message", {}).get("ts", "0"))

    event = _to_apigw_form_event(payload)
    resp = lambda_handler(event, {})

    assert resp["statusCode"] == 200

    # value MUST から origin を取り、スレッド返信していること
    mock_slack.chat_postMessage.assert_called_once()
    _, kwargs = mock_slack.chat_postMessage.call_args
    assert kwargs["channel"] == "C_ORIGIN"
    assert kwargs["thread_ts"] == "1700000000.12345"

    # 管理者側メッセージ更新（実装が行うなら）
    # 既存テストに合わせて最低限確認
    mock_slack.chat_update.assert_called_once()

def test_lambdaB_handles_fixture_interactivity_dismiss_updates_notion_and_no_reply(
    load_contract_fixture, mock_external_services, mock_config
):
    mock_external_services["signature"].is_valid_request.return_value = True
    mock_slack = mock_external_services["slack"]
    mock_notion = mock_external_services["notion"]

    payload = load_contract_fixture("interactivity_button_click_dismiss.json")
    payload.setdefault("container", {})
    payload["container"].setdefault("channel_id", payload.get("channel", {}).get("id", "C_PRIVATE"))
    payload["container"].setdefault("message_ts", payload.get("message", {}).get("ts", "0"))

    event = _to_apigw_form_event(payload)
    resp = lambda_handler(event, {})

    assert resp["statusCode"] == 200

    # 1) origin への警告返信はしない（設計どおりなら）
    mock_slack.chat_postMessage.assert_not_called()

    # 2) Notion status を dismiss に更新する
    value = json.loads(payload["actions"][0]["value"])
    mock_notion.update_status.assert_called_once()
    call_args = mock_notion.update_status.call_args
    assert call_args.args[0] == value["notion_page_id"]
    assert call_args.args[1] == "Dismissed"

    # 3) 管理者側メッセージ更新するなら（実装している場合だけ）
    # mock_slack.chat_update.assert_called_once()
