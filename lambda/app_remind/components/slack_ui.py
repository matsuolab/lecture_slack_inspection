"""48h経過通知のSlack UI blocks構築"""
from __future__ import annotations
from typing import Any

REWARN_TEMPLATE_BLOCK_ID = "rewarn_template"
REWARN_TEMPLATE_ACTION_ID = "rewarn_template_select"

REWARN_ACTION_ID = "rewarn_violation"
CLOSE_ACTION_ID = "close_violation"

DEFAULT_REWARN_TEMPLATE_OPTIONS: list[tuple[str, str]] = [
    ("デフォルト再警告文", ""),
]


def _escape_mrkdwn(text: str) -> str:
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )


def _truncate_one_line(text: str, max_len: int) -> str:
    t = (text or "").strip().replace("\n", " ")
    if len(t) <= max_len:
        return t
    return t[:max_len] + "…"


def build_48h_alert_blocks(
    *,
    post_link: str | None,
    post_text: str,
    poster: str | None,
    origin_channel_id: str,
    button_value: str,
    rewarn_template_options: list[tuple[str, str]] | None = None,
    max_post_len: int = 200,
) -> list[dict[str, Any]]:
    """48h経過通知のblocks（管理ch初回通知へのスレッド返信用）"""

    link_part = f"<{post_link}|元投稿を開く>" if post_link else "（リンク取得失敗）"
    poster_part = _escape_mrkdwn(poster) if poster else "（不明）"
    channel_part = f"<#{origin_channel_id}>" if origin_channel_id else "（不明）"
    safe_post = _escape_mrkdwn(_truncate_one_line(post_text, max_post_len))

    opts = rewarn_template_options or DEFAULT_REWARN_TEMPLATE_OPTIONS
    slack_options = [
        {"text": {"type": "plain_text", "text": label}, "value": value}
        for (label, value) in opts
    ]
    initial_option = slack_options[0] if slack_options else None

    blocks: list[dict[str, Any]] = []

    blocks.append(
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "⏰ *48時間経過：投稿が未削除です*",
            },
            "fields": [
                {"type": "mrkdwn", "text": f"*投稿者*\n{poster_part}"},
                {"type": "mrkdwn", "text": f"*投稿チャンネル*\n{channel_part}"},
                {"type": "mrkdwn", "text": f"*投稿先(URL)*\n{link_part}"},
            ],
        }
    )

    blocks.append({"type": "divider"})

    blocks.append(
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*投稿内容（先頭{max_post_len}文字）*\n> {safe_post}",
            },
        }
    )

    blocks.append({"type": "divider"})

    blocks.append(
        {
            "type": "section",
            "block_id": REWARN_TEMPLATE_BLOCK_ID,
            "text": {
                "type": "mrkdwn",
                "text": "*再警告テンプレート*\n送信する再警告文のテンプレを選択してください。",
            },
            "accessory": {
                "type": "static_select",
                "action_id": REWARN_TEMPLATE_ACTION_ID,
                "placeholder": {"type": "plain_text", "text": "テンプレを選択"},
                "options": slack_options,
                **({"initial_option": initial_option} if initial_option else {}),
            },
        }
    )

    blocks.append(
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "再警告を送る"},
                    "style": "danger",
                    "action_id": REWARN_ACTION_ID,
                    "value": button_value,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "対応終了"},
                    "action_id": CLOSE_ACTION_ID,
                    "value": button_value,
                },
            ],
        }
    )

    return blocks
