"""ISS-15 失敗に気づく仕組みが弱い（アラーム・DLQ が無い、ヘルスチェックは上書き、判定の失敗はメトリクスにならない）

docs/requirements/issues.md: ISS-15 失敗に気づけない
今の動きを固定する特性テスト。不具合を直したら期待値を書き換えること。
"""

from unittest.mock import MagicMock

from common.notion_client import NotionClient


def test_write_health_status_overwrites_existing_row_and_clears_error(mocker):
    """write_health_status をエラー→正常の順に呼んだとき、同じ既存行が上書き更新され 2 回目の更新でエラー内容が空になることを確かめる。"""
    client = NotionClient(
        api_key="secret_test",
        db_id="dummy_db",
        health_db_id="dummy_health_db",
    )

    mock_post = mocker.patch("common.notion_client.requests.post")
    mock_patch = mocker.patch("common.notion_client.requests.patch")

    # 検索結果として常に health-row-1 を返す
    mock_query_resp = MagicMock()
    mock_query_resp.ok = True
    mock_query_resp.json.return_value = {"results": [{"id": "health-row-1"}]}
    mock_post.return_value = mock_query_resp

    mock_patch_resp = MagicMock()
    mock_patch_resp.ok = True
    mock_patch.return_value = mock_patch_resp

    # 1回目: エラー
    res1 = client.write_health_status("app_inspect", "エラー", error_message="Fatal crash")
    assert res1 is True

    # 2回目: 正常
    res2 = client.write_health_status("app_inspect", "正常")
    assert res2 is True

    # 2回とも同じ health-row-1 に対して patch が実行されたこと
    assert mock_patch.call_count == 2
    call1_url = mock_patch.call_args_list[0][0][0]
    call1_kwargs = mock_patch.call_args_list[0][1]
    call2_url = mock_patch.call_args_list[1][0][0]
    call2_kwargs = mock_patch.call_args_list[1][1]

    assert call1_url == "https://api.notion.com/v1/pages/health-row-1"
    assert call2_url == "https://api.notion.com/v1/pages/health-row-1"

    # 1回目はエラー内容あり
    props1 = call1_kwargs["json"]["properties"]
    assert props1["ステータス"]["select"]["name"] == "エラー"
    assert props1["エラー内容"]["rich_text"][0]["text"]["content"] == "Fatal crash"

    # 2回目の更新でエラー内容が空（{"rich_text": []}）になっていること
    props2 = call2_kwargs["json"]["properties"]
    assert props2["ステータス"]["select"]["name"] == "正常"
    assert props2["エラー内容"] == {"rich_text": []}

    # 新規作成（/pages への post）は一度も呼ばれていない（post は検索の /databases/.../query のみ）
    for call in mock_post.call_args_list:
        assert "/databases/dummy_health_db/query" in call[0][0]
