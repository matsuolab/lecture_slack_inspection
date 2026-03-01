from __future__ import annotations
from typing import Any

def _escape_mrkdwn(text: str) -> str:
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "…"


def build_violation_alert_blocks(
    *,
    origin_channel_id: str,
    post_link: str | None,
    post_text: str,
    rationale: str,
    button_value: str,
    max_post_len: int = 200,
) -> list[dict[str, Any]]:
    """
    運営向け「違反の可能性を検知」アラートの blocks を返す。

    Args:
        origin_channel_id: 元投稿の channel_id
        post_link: chat_getPermalinkで取得した元投稿URL（取れない場合None）
        post_text: 元投稿本文（ユーザー入力）
        rationale: 違反理由（LLM出力）
        button_value: approve/dismiss ボタンに埋め込む value(JSON文字列)
        max_post_len: 本文の表示最大長
    """
    safe_post = _escape_mrkdwn(_truncate(post_text.strip().replace("\n", " "), max_post_len))
    safe_rationale = _escape_mrkdwn(rationale.strip())

    channel_part = f"*チャンネル*: <#{origin_channel_id}>"
    if post_link:
        post_part = f"*投稿*: <{post_link}|元投稿を開く>"
    else:
        post_part = "*投稿*: （リンク取得失敗）"

    section_text = (
        "🚨 *違反の可能性を検知*\n"
        f"{channel_part}\n"
        f"{post_part}\n"
        f"*内容*: {safe_post}\n"
        f"*理由*: {safe_rationale}"
    )

    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": section_text},
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "削除勧告を送る"},
                    "style": "danger",
                    "action_id": "approve_violation",
                    "value": button_value,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Dismiss（対応不要）"},
                    "action_id": "dismiss_violation",
                    "value": button_value,
                },
            ],
        },
    ]
