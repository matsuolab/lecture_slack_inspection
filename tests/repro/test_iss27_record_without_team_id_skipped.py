"""ISS-27 team_id の無いレコードを、定期処理が黙って永久にスキップする

docs/requirements/issues.md: ISS-27 team_id の無いレコードを定期処理がスキップする
今の動きを固定する特性テスト。不具合を直したら期待値を書き換えること。
"""

import os
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

from common.notion_client import NotionClient
from app_remind.handler import _build_workspace_clients
from app_remind.services.reminder import process_reminders


def test_process_reminders_skips_record_without_team_id(monkeypatch):
    """SLACK_BOT_TOKEN__* 環境変数が無く team_id が空の警告済みレコードがあるとき、skipped_no_client が 1 となり状態更新されないことを確かめる。"""
    # SLACK_BOT_TOKEN__* の環境変数を削除
    for key in list(os.environ.keys()):
        if key.startswith("SLACK_BOT_TOKEN__"):
            monkeypatch.delenv(key, raising=False)

    workspace_clients = _build_workspace_clients()
    assert workspace_clients == {}

    one_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    page = {
        "id": "page-no-team-id",
        "properties": {
            "投稿内容": {"title": [{"plain_text": "古いレコード"}]},
            "投稿者": {"rich_text": [{"plain_text": "U_TEST"}]},
            "投稿リンク": {"url": "https://test.slack.com/archives/C0TEST/p1700000000000100"},
            "警告送信日時": {"date": {"start": one_hour_ago}},
            "team_id": {"rich_text": []},
        },
    }

    mock_notion = MagicMock(spec=NotionClient)
    mock_notion.query_approved_unreminded.return_value = [page]
    mock_notion.extract_reminder_fields = NotionClient.extract_reminder_fields
    mock_notion.parse_slack_link = NotionClient.parse_slack_link
    mock_notion.is_past_threshold = NotionClient.is_past_threshold

    stats = process_reminders(
        notion=mock_notion,
        hours_threshold=48,
        slack_clients=workspace_clients,
    )

    assert stats["skipped_no_client"] == 1
    mock_notion.mark_closed.assert_not_called()
    mock_notion.mark_48h_over.assert_not_called()
