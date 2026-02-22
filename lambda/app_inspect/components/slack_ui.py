from typing import Optional

def build_alert_blocks(
    channel_id: str,
    post_link: Optional[str],
    text: str,
    rationale: str,
    button_value: str
) -> list[dict]:

    # リンクのフォールバック処理をUI層で完結させる
    if post_link and post_link != "取得不可":
        link_text = f"<{post_link}|元投稿を開く>"
    else:
        link_text = "リンク取得不可"

    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "🚨 *違反の可能性を検知*\n"
                    f"*チャンネル*: <#{channel_id}>\n"
                    f"*投稿*: {link_text}\n"
                    f"*内容*: {text[:200]}\n"
                    f"*理由*: {rationale}"
                ),
            },
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


def build_system_error_blocks(
    user_id: str,
    text: str,
    rationale: str,
    trace_id: str
) -> list[dict]:

    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "🚨 *システムエラー: Notion記録失敗*\n"
                    "記録に失敗したため、アクションボタンの生成を中止しました。手動での対応が必要です。\n"
                    f"*ユーザー*: <@{user_id}>\n"
                    f"*内容*: {text[:200]}\n"
                    f"*判定理由*: {rationale}\n"
                    f"*Trace ID*: `{trace_id}`"
                )
            }
        }
    ]
