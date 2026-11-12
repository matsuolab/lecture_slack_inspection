"""
ISS-13: 重複の確認が失敗すると「重複なし」になる。Message_TS はチャンネルをまたいで一意でない
docs/requirements/issues.md の該当の節: ## 詳細 > ### ISS-13 重複の確認

今の動きを固定する特性テスト。不具合を直したら期待値を書き換えること。
"""

from unittest.mock import MagicMock
import pytest
import requests
from common.notion_client import NotionClient


@pytest.fixture
def notion_client():
    return NotionClient(
        api_key="secret_test",
        db_id="dummy_db_id",
        articles_db_id="dummy_articles_db",
        ws_list_db_id="dummy_ws_list_db",
    )


def test_duplicate_check_post_not_ok_returns_false(notion_client, mocker):
    """requests.post が ok=False (500) を返した場合、check_duplicate_violation は False を返すことを確認。"""
    mock_resp = MagicMock()
    mock_resp.ok = False
    mock_resp.status_code = 500
    mock_resp.text = "Internal Server Error"
    mocker.patch("common.notion_client.requests.post", return_value=mock_resp)

    result = notion_client.check_duplicate_violation("1700000000.000100")
    assert result is False


def test_duplicate_check_post_exception_returns_false(notion_client, mocker):
    """requests.post が例外を投げた場合、check_duplicate_violation は False を返すことを確認。"""
    mocker.patch(
        "common.notion_client.requests.post",
        side_effect=requests.RequestException("Network connection error"),
    )

    result = notion_client.check_duplicate_violation("1700000000.000100")
    assert result is False


def test_duplicate_check_and_find_violation_filter_only_has_message_ts(notion_client, mocker):
    """check_duplicate_violation と find_violation_by_message_ts が送信する filter に Message_TS しか含まれず、チャンネルや team_id が含まれないことを確認。"""
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.json.return_value = {"results": [], "has_more": False}
    mock_post = mocker.patch("common.notion_client.requests.post", return_value=mock_resp)

    # 1. check_duplicate_violation の検証
    notion_client.check_duplicate_violation("1700000000.000100")
    assert mock_post.call_count == 1
    call_json_dup = mock_post.call_args.kwargs.get("json", {})
    filter_dup = call_json_dup.get("filter", {})
    assert filter_dup == {
        "property": "Message_TS",
        "rich_text": {"equals": "1700000000.000100"},
    }
    assert "channel" not in filter_dup
    assert "team_id" not in filter_dup

    # 2. find_violation_by_message_ts の検証
    mock_post.reset_mock()
    notion_client.find_violation_by_message_ts("1700000000.000100")
    assert mock_post.call_count == 1
    call_json_find = mock_post.call_args.kwargs.get("json", {})
    filter_find = call_json_find.get("filter", {})
    assert filter_find == {
        "property": "Message_TS",
        "rich_text": {"equals": "1700000000.000100"},
    }
    assert "channel" not in filter_find
    assert "team_id" not in filter_find
