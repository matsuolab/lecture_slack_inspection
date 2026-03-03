"""Slackメッセージ・ボタン構築ユーティリティ"""
import json


def encode_alert_button_value(notion_page_id: str | None, **kwargs) -> str:
    """Slackボタンの value に埋め込むデータをJSON化する。

    Args:
        notion_page_id: Notionページ ID（任意）
        **kwargs: ボタン value に含める任意の追加フィールド

    Note:
        Slack の value フィールドは2000文字制限があるため注意。
    """
    data = kwargs
    if notion_page_id:
        data["notion_page_id"] = notion_page_id
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))
