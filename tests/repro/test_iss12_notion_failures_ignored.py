"""ISS-12 Notion への記録・更新の失敗を無視して続ける。警告したのに状態が 未対応 のまま、などが起きる

docs/requirements/issues.md: ISS-12 Notion の失敗を無視して続ける
今の動きを固定する特性テスト。不具合を直したら期待値を書き換えること。
"""

import json
from unittest.mock import MagicMock

from common.notion_client import NotionClient
from common.observability import build_context
from app_inspect.services.inspection_flow import handle_new_message
from app_inspect.services.models import InspectEvent, ModerationResult
from app_alert.services.actions import parse_action_context, handle_approve_violation


def test_handle_new_message_sends_alert_without_notion_page_id_when_notion_create_fails(
    mock_config, mocker
):
    """Notion への記録が None を返しても警告通知が送信され、ボタンの value に notion_page_id が含まれないことを確かめる。"""
    mock_context = build_context({}, None, "app_inspect")
    mock_slack = MagicMock()
    mock_slack.chat_getPermalink.return_value = {
        "permalink": "https://test.slack.com/archives/C_TEST/p1700000000000100"
    }
    mock_slack.chat_postMessage.return_value = {"ok": True, "ts": "1700000000.999999"}
    mock_slack.users_info.return_value = {
        "user": {"real_name": "Test User", "profile": {"display_name": "test"}}
    }
    mock_slack.conversations_info.return_value = {"channel": {"name": "test-channel"}}
    mock_slack.team_info.return_value = {"team": {"name": "test-team"}}

    mock_notion = MagicMock(spec=NotionClient)
    mock_notion.check_duplicate_violation.return_value = False
    mock_notion.create_violation_log.return_value = None
    mock_notion.find_article_page_id.return_value = None

    event = InspectEvent(
        kind="new_message",
        text="不適切な勧誘投稿",
        user_id="U_TEST",
        channel_id="C_TEST",
        message_ts="1700000000.000100",
        team_id="T_TEST",
    )
    mod_result = ModerationResult(
        is_violation=True,
        severity="high",
        rationale="勧誘の疑い",
        recommended_reply="",
        categories=["勧誘行為"],
        article_id="11-v",
        confidence=0.9,
    )

    resp = handle_new_message(
        context=mock_context,
        cfg=mock_config,
        inspect_event=event,
        slack_client=mock_slack,
        notion=mock_notion,
        moderate_text=lambda text: mod_result,
    )

    assert resp["statusCode"] == 200
    mock_slack.chat_postMessage.assert_called_once()
    call_kwargs = mock_slack.chat_postMessage.call_args.kwargs
    blocks = call_kwargs.get("blocks", [])

    button_value = None
    for block in blocks:
        if block.get("type") == "actions":
            for element in block.get("elements", []):
                if "value" in element:
                    button_value = json.loads(element["value"])
                    break

    assert button_value is not None
    assert "notion_page_id" not in button_value or not button_value["notion_page_id"]


def test_handle_approve_violation_returns_true_even_when_notion_update_fails(
    load_contract_fixture, mocker
):
    """Notion の update_status が False を返しても handle_approve_violation が True を返しボタンが消える（chat_update が呼ばれる）ことを確かめる。"""
    payload = load_contract_fixture("interactivity_button_click_approve.json")
    context = parse_action_context(payload)
    assert context is not None

    mock_slack = MagicMock()
    mock_notion = MagicMock(spec=NotionClient)
    mock_notion.update_status.return_value = False

    mocker.patch("app_alert.services.actions.resolve_template", return_value="警告テンプレート本文")

    result = handle_approve_violation(
        context=context,
        slack=mock_slack,
        notion=mock_notion,
        responder_id="U_ADMIN_01",
    )

    assert result is True
    mock_notion.update_status.assert_called_once()
    mock_slack.chat_update.assert_called_once()
