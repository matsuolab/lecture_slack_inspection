"""ISS-04 投稿の存在確認が Slack API のエラーを「削除された」と扱い、誤って対応終了にする

docs/requirements/issues.md: ISS-04 存在確認のエラーを「削除された」と扱う
今の動きを固定する特性テスト。不具合を直したら期待値を書き換えること。
"""

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock
import pytest
from slack_sdk.errors import SlackApiError

from common.notion_client import NotionClient
from app_remind.services.reminder import (
    check_message_exists,
    process_reminders,
    process_deletion_check,
)


@pytest.mark.parametrize(
    "error_code",
    [
        "ratelimited",
        "not_in_channel",
        "account_inactive",
        "channel_not_found",
    ],
)
def test_check_message_exists_returns_false_on_slack_api_errors(error_code):
    """conversations_history が SlackApiError を投げたとき、check_message_exists が False を返すことを確かめる。"""
    mock_slack = MagicMock()
    mock_response = MagicMock()
    mock_response.data = {"ok": False, "error": error_code}
    mock_response.__getitem__.side_effect = lambda k: mock_response.data[k]
    mock_response.get.side_effect = mock_response.data.get
    mock_slack.conversations_history.side_effect = SlackApiError(
        f"Slack API error: {error_code}", mock_response
    )

    result = check_message_exists(mock_slack, "C0TEST", "1700000000.000100")
    assert result is False


def test_process_reminders_closes_record_on_ratelimited_slack_error(mocker):
    """警告済みレコードの存在確認で SlackApiError(ratelimited) が発生したとき、mark_closed と管理者への削除通知が呼ばれることを確かめる。"""
    one_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    page = {
        "id": "page-test-01",
        "properties": {
            "投稿内容": {"title": [{"plain_text": "テスト投稿"}]},
            "投稿者": {"rich_text": [{"plain_text": "U_TEST"}]},
            "投稿リンク": {"url": "https://test.slack.com/archives/C0TEST/p1700000000000100"},
            "警告送信日時": {"date": {"start": one_hour_ago}},
            "team_id": {"rich_text": [{"plain_text": "T_TEST"}]},
            "通知チャンネルID": {"rich_text": [{"plain_text": "C_ADMIN"}]},
            "通知メッセージTS": {"rich_text": [{"plain_text": "1700000000.999999"}]},
        },
    }

    mock_notion = MagicMock(spec=NotionClient)
    mock_notion.query_approved_unreminded.return_value = [page]
    mock_notion.extract_reminder_fields = NotionClient.extract_reminder_fields
    mock_notion.parse_slack_link = NotionClient.parse_slack_link
    mock_notion.is_past_threshold = NotionClient.is_past_threshold
    mock_notion.mark_closed.return_value = True

    mock_slack = MagicMock()
    mock_response = MagicMock()
    mock_response.data = {"ok": False, "error": "ratelimited"}
    mock_response.__getitem__.side_effect = lambda k: mock_response.data[k]
    mock_response.get.side_effect = mock_response.data.get
    mock_slack.conversations_history.side_effect = SlackApiError(
        "Slack API error: ratelimited", mock_response
    )

    mocker.patch("app_remind.services.reminder.get_parameter_by_name", return_value="xoxb-test")
    mocker.patch("app_remind.services.reminder.WebClient", return_value=mock_slack)

    stats = process_reminders(notion=mock_notion, hours_threshold=48)

    assert stats["already_deleted"] == 1
    mock_notion.mark_closed.assert_called_once_with("page-test-01")
    mock_slack.chat_postMessage.assert_called_once_with(
        channel="C_ADMIN",
        thread_ts="1700000000.999999",
        text="✅ 投稿が削除されました。対応ステータスを「対応終了」に更新しました。",
    )


def test_process_deletion_check_closes_record_on_ratelimited_slack_error(mocker):
    """期限超過レコードの削除確認で SlackApiError(ratelimited) が発生したとき、mark_closed と管理者への削除通知が呼ばれることを確かめる。"""
    page = {
        "id": "page-test-02",
        "properties": {
            "投稿内容": {"title": [{"plain_text": "テスト投稿2"}]},
            "投稿者": {"rich_text": [{"plain_text": "U_TEST"}]},
            "投稿リンク": {"url": "https://test.slack.com/archives/C0TEST/p1700000000000200"},
            "team_id": {"rich_text": [{"plain_text": "T_TEST"}]},
            "通知チャンネルID": {"rich_text": [{"plain_text": "C_ADMIN"}]},
            "通知メッセージTS": {"rich_text": [{"plain_text": "1700000000.888888"}]},
        },
    }

    mock_notion = MagicMock(spec=NotionClient)
    mock_notion.query_active_violations.return_value = [page]
    mock_notion.extract_reminder_fields = NotionClient.extract_reminder_fields
    mock_notion.parse_slack_link = NotionClient.parse_slack_link
    mock_notion.mark_closed.return_value = True

    mock_slack = MagicMock()
    mock_response = MagicMock()
    mock_response.data = {"ok": False, "error": "ratelimited"}
    mock_response.__getitem__.side_effect = lambda k: mock_response.data[k]
    mock_response.get.side_effect = mock_response.data.get
    mock_slack.conversations_history.side_effect = SlackApiError(
        "Slack API error: ratelimited", mock_response
    )

    mocker.patch("app_remind.services.reminder.get_parameter_by_name", return_value="xoxb-test")
    mocker.patch("app_remind.services.reminder.WebClient", return_value=mock_slack)

    stats = process_deletion_check(notion=mock_notion)

    assert stats["deleted"] == 1
    mock_notion.mark_closed.assert_called_once_with("page-test-02")
    mock_slack.chat_postMessage.assert_called_once_with(
        channel="C_ADMIN",
        thread_ts="1700000000.888888",
        text="✅ 投稿が削除されました。対応ステータスを「対応終了」に更新しました。",
    )
