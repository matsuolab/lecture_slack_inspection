"""
ISS-08: subtype 付きの投稿（ファイル付きの投稿など）を判定していない
docs/requirements/issues.md の該当の節: ## 詳細 > ### ISS-08 subtype 付きの投稿を判定していない

今の動きを固定する特性テスト。不具合を直したら期待値を書き換えること。
"""

import pytest
from app_inspect.components.slack_event_parser import parse_slack_message_event


@pytest.mark.parametrize("subtype", ["file_share", "thread_broadcast", "me_message"])
def test_subtype_messages_ignored(subtype):
    """subtype 付きのメッセージイベントは、本文に勧誘文があっても parse_slack_message_event で無視され None となることを確認。"""
    body_json = {
        "type": "event_callback",
        "team_id": "T_TEST",
        "event": {
            "type": "message",
            "channel": "C_TEST",
            "user": "U_TEST",
            "text": "誰でも簡単に稼げる副業セミナーはこちら！今すぐ登録してください。",
            "ts": "1700000000.000100",
            "subtype": subtype,
        },
    }
    event = parse_slack_message_event(body_json)
    assert event is None


def test_contrast_message_without_subtype_parsed():
    """対照テスト: subtype が無い通常メッセージは正常に parse_slack_message_event でパースされることを確認。"""
    body_json = {
        "type": "event_callback",
        "team_id": "T_TEST",
        "event": {
            "type": "message",
            "channel": "C_TEST",
            "user": "U_TEST",
            "text": "誰でも簡単に稼げる副業セミナーはこちら！今すぐ登録してください。",
            "ts": "1700000000.000100",
        },
    }
    event = parse_slack_message_event(body_json)
    assert event is not None
    assert event.kind == "new_message"
    assert event.text == "誰でも簡単に稼げる副業セミナーはこちら！今すぐ登録してください。"
