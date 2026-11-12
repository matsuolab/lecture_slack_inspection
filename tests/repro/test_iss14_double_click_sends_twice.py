"""ISS-14 ボタンの二重押しで警告が二重に送られる。押した人の権限を確かめない。終わったレコードのボタンが残る

docs/requirements/issues.md: ISS-14 ボタンの二重押し・権限・残ったボタン
今の動きを固定する特性テスト。不具合を直したら期待値を書き換えること。
"""

from unittest.mock import MagicMock

from common.notion_client import NotionClient
from app_alert.services.actions import parse_action_context, handle_approve_violation


def test_double_click_approve_sends_warning_twice_without_checking_notion(
    load_contract_fixture, mocker
):
    """同じ ActionContext で handle_approve_violation を 2 回呼んだとき、元投稿への警告が 2 回送信され Notion のレコード読み取りが行われないことを確かめる。"""
    payload = load_contract_fixture("interactivity_button_click_approve.json")
    context = parse_action_context(payload)
    assert context is not None

    mock_slack = MagicMock()
    mock_notion = MagicMock(spec=NotionClient)
    mock_notion.update_status.return_value = True

    mocker.patch("app_alert.services.actions.resolve_template", return_value="警告テンプレート本文")

    res1 = handle_approve_violation(
        context=context,
        slack=mock_slack,
        notion=mock_notion,
        responder_id="U_ADMIN_01",
    )
    res2 = handle_approve_violation(
        context=context,
        slack=mock_slack,
        notion=mock_notion,
        responder_id="U_ADMIN_01",
    )

    assert res1 is True
    assert res2 is True

    # 元投稿への警告が2回送られる
    assert mock_slack.chat_postMessage.call_count == 2
    for call in mock_slack.chat_postMessage.call_args_list:
        assert call.kwargs["channel"] == "C_ORIGIN"
        assert call.kwargs["thread_ts"] == "1700000000.12345"

    # Notionのレコード読み取り系メソッドが1回も呼ばれない
    mock_notion.find_violation_by_message_ts.assert_not_called()
    mock_notion.get_article_name.assert_not_called()
