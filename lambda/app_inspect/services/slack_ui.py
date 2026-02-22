from typing import Optional
from .models import ModerationResult

def build_alert_blocks(
    channel_id: str,
    post_link: Optional[str],
    text: str,
    rationale: str,
    button_value: str
) -> list[dict]:
    """
    違反検知時の運営向けアクションボタン付きブロックを構築する。
    
    Args:
        channel_id (str): 検知対象のチャンネルID
        post_link (str | None): 元投稿のPermalink（取得失敗時はNoneまたは"取得不可"）
        text (str): 投稿テキストの先頭部分
        rationale (str): AIによる違反判定の理由
        button_value (str): ボタン押下時に送信されるJSONペイロード文字列

    Returns:
        list[dict]: Slack API (chat.postMessage) に渡す blocks リスト
    """
    # リンクのフォールバック処理をUI層に集約し、呼び出し元の複雑性を排除する
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
    result: ModerationResult,
    trace_id: str
) -> list[dict]:
    """
    Notion起票失敗などのシステム異常時に、アクションボタンを持たないエラーブロックを構築する。
    
    Args:
        user_id (str): 投稿者のSlackユーザーID
        text (str): 投稿テキストの先頭部分
        result (ModerationResult): モデレーションの判定結果オブジェクト
        trace_id (str): ログ追跡用のTrace ID

    Returns:
        list[dict]: Slack API (chat.postMessage) に渡す blocks リスト
    """
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "🚨 *システムエラー: 状態管理DB(Notion)起票失敗*\n"
                    "記録に失敗したため、アクションボタンの生成を中止しました。手動での対応が必要です。\n"
                    f"*ユーザー*: <@{user_id}>\n"
                    f"*内容*: {text[:200]}\n"
                    f"*判定理由*: {result.rationale}\n"
                    f"*Trace ID*: `{trace_id}`"
                )
            }
        }
    ]
