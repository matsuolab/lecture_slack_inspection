"""
ISS-28: ローカルルールの取得がページ送りをせず、1 回の応答の分しか読まない
docs/requirements/issues.md の該当の節: ## 詳細 > ### ISS-28 ローカルルールの取得がページ送りをしない

今の動きを固定する特性テスト。不具合を直したら期待値を書き換えること。
"""

from unittest.mock import MagicMock
import pytest
from common.notion_client import NotionClient


def test_query_workspace_local_rules_does_not_paginate_even_if_has_more(mocker):
    """query_workspace_local_rules は has_more=True でも requests.post を 1 回しか呼ばずページネーションを行わないことを確認。"""
    client = NotionClient(
        api_key="secret_test",
        db_id="dummy_db_id",
        articles_db_id="dummy_articles_db_id",
        ws_list_db_id="dummy_ws_list_db_id",
    )

    article_item = {
        "properties": {
            "条文ID": {"title": [{"plain_text": "local-rule-01"}]},
            "条項番号": {"rich_text": [{"plain_text": "ローカル規則 第1条"}]},
            "内容": {"rich_text": [{"plain_text": "ローカルルール禁止事項"}]},
            "カテゴリ": {"select": {"name": "独自カテゴリ"}},
            "規約": {"select": {"name": "WS独自規約"}},
        }
    }

    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "results": [article_item],
        "has_more": True,
        "next_cursor": "c1",
    }
    mock_post = mocker.patch("common.notion_client.requests.post", return_value=mock_resp)

    rules = client.query_workspace_local_rules("ws-page-1")

    assert mock_post.call_count == 1
    assert len(rules) == 1
    assert rules[0]["id"] == "local-rule-01"
    assert rules[0]["article"] == "ローカル規則 第1条"
    assert rules[0]["content"] == "ローカルルール禁止事項"
