from __future__ import annotations
from typing import Any, Iterable

WARNING_TEMPLATE_BLOCK_ID = "warning_template"
WARNING_TEMPLATE_ACTION_ID = "warning_template_select"

APPROVE_ACTION_ID = "approve_violation"
DISMISS_ACTION_ID = "dismiss_violation"

# テンプレートDBから取得できない場合のフォールバック選択肢
DEFAULT_WARNING_TEMPLATE_OPTIONS: list[tuple[str, str]] = [
    ("デフォルト警告文", ""),
]


def _escape_mrkdwn(text: str) -> str:
    # ユーザー入力の最低限エスケープ（リンク/メンション注入を抑える）
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


def _format_slack_datetime(ts: str | None) -> str:
    """
    Slackの ts(例: "1548261231.000200") を mrkdwn の date フォーマットにする。
    表示は「日付 + 時:分」（秒なし）にするため {time} を使用する。
    失敗時は ts をそのまま表示。
    """
    if not ts:
        return "（不明）"
    try:
        unix_sec = int(float(ts))
        fallback = ts
        return f"<!date^{unix_sec}^{{date_short_pretty}} {{time}}|{fallback}>"
    except Exception:
        return ts


def _join_list(items: Iterable[str] | None) -> str:
    if not items:
        return "（なし）"
    xs = [str(x).strip() for x in items if str(x).strip()]
    return ", ".join(xs) if xs else "（なし）"


def build_violation_alert_blocks(
    *,
    author_user_id: str | None,
    author_display: str | None,
    origin_channel_id: str,
    post_link: str | None,
    post_ts: str | None,
    post_text: str,
    detection_method: str,
    confidence: float | None,
    rationale: str,
    guideline_article: str | None,
    categories: list[str] | None,
    button_value: str,
    warning_template_options: list[tuple[str, str]] | None = None,
    initial_warning_template_value: str | None = None,
    max_post_len: int = 200,
) -> list[dict[str, Any]]:
    # ---- 表示用整形 ----
    author_part = "（不明）"
    if author_user_id:
        author_part = f"<@{author_user_id}>"
        if author_display:
            safe_disp = _escape_mrkdwn(author_display.strip())
            if safe_disp and safe_disp != f"@{author_user_id}":
                author_part = f"{author_part}（{safe_disp}）"
    elif author_display:
        author_part = _escape_mrkdwn(author_display.strip())

    channel_part = f"<#{origin_channel_id}>" if origin_channel_id else "（不明）"
    link_part = f"<{post_link}|元投稿を開く>" if post_link else "（リンク取得失敗）"
    posted_at_part = _format_slack_datetime(post_ts)

    safe_post = _escape_mrkdwn(_truncate_one_line(post_text, max_post_len))
    safe_rationale = _escape_mrkdwn(_truncate_one_line(rationale, 500))
    method_part = _escape_mrkdwn(detection_method.strip() or "（不明）")

    conf_part = None
    if confidence is not None:
        try:
            conf_part = f"{float(confidence) * 100:.0f}%"
        except Exception:
            conf_part = str(confidence)

    article_part = _escape_mrkdwn(guideline_article.strip()) if guideline_article else "（不明）"
    category_part = _escape_mrkdwn(_join_list(categories))

    # テンプレ options
    opts = warning_template_options or DEFAULT_WARNING_TEMPLATE_OPTIONS
    slack_options = [
        {"text": {"type": "plain_text", "text": label}, "value": value}
        for (label, value) in opts
    ]

    initial_option = None
    if initial_warning_template_value:
        for o in slack_options:
            if o["value"] == initial_warning_template_value:
                initial_option = o
                break
    if initial_option is None and slack_options:
        initial_option = slack_options[0]

    # ---- blocks ----
    blocks: list[dict[str, Any]] = []

    blocks.append(
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": "🚨 *違反の可能性を検知*"},
            "fields": [
                {"type": "mrkdwn", "text": f"*投稿者*\n{author_part}"},
                {"type": "mrkdwn", "text": f"*投稿チャンネル*\n{channel_part}"},
                {"type": "mrkdwn", "text": f"*投稿先(URL)*\n{link_part}"},
                {"type": "mrkdwn", "text": f"*投稿日*\n{posted_at_part}"},
            ],
        }
    )

    blocks.append({"type": "divider"})

    blocks.append(
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*投稿内容（先頭{max_post_len}文字）*\n> {safe_post}"},
        }
    )

    reason_lines = [f"• *検出方法*: {method_part}"]
    if conf_part is not None:
        reason_lines.append(f"• *確信度*: {conf_part}")
    reason_lines.append(f"• *判定理由*: {safe_rationale}")

    blocks.append(
        {"type": "section", "text": {"type": "mrkdwn", "text": "*検出理由*\n" + "\n".join(reason_lines)}}
    )

    blocks.append(
        {
            "type": "context",
            "elements": [
                {"type": "mrkdwn", "text": f"*該当条文*: {article_part}    |    *カテゴリ*: {category_part}"}
            ],
        }
    )

    blocks.append({"type": "divider"})

    blocks.append(
        {
            "type": "section",
            "block_id": WARNING_TEMPLATE_BLOCK_ID,
            "text": {"type": "mrkdwn", "text": "*警告文テンプレート*\n送信する警告文のテンプレを選択してください。"},
            "accessory": {
                "type": "static_select",
                "action_id": WARNING_TEMPLATE_ACTION_ID,
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
                    "text": {"type": "plain_text", "text": "削除勧告を送る"},
                    "style": "danger",
                    "action_id": APPROVE_ACTION_ID,
                    "value": button_value,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Dismiss（対応不要）"},
                    "action_id": DISMISS_ACTION_ID,
                    "value": button_value,
                },
            ],
        }
    )

    return blocks
