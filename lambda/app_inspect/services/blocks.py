import json
import os
import re
from typing import Any, Optional


POLICY_BLOCK_ID = "policy_ref_block"
AID_REGULATION = "policy_regulation_select"
AID_ARTICLE = "policy_article_select"
AID_ITEM = "policy_item_select"

ARTICLES_JSON_PATH = os.path.join(
    os.path.dirname(__file__),
    "..", "..", "common", "data", "articles.json"
)

_ROMAN = {
    "i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6,
    "vii": 7, "viii": 8, "ix": 9, "x": 10, "xi": 11, "xii": 12,
    "xiii": 13, "xiv": 14, "xv": 15, "xvi": 16,
}


def _load_articles() -> dict[str, Any]:
    with open(ARTICLES_JSON_PATH, encoding="utf-8") as f:
        return json.load(f)


def _parse_article_id(article_id: str) -> tuple[Optional[int], Optional[int]]:
    """
    examples:
      "11-iv" -> (11, 4)
      "course-8-ii" -> (8, 2)
      "edu-6" -> (6, None)
      "course-drive-url" -> (None, None)  # 条/項で表現できない特則
    """
    if not article_id:
        return None, None

    m = re.search(r"-(\d+)(?:-([a-z]+))?$", article_id)
    if not m:
        return None, None

    article_no = int(m.group(1))
    roman = m.group(2)
    item_no = _ROMAN.get(roman) if roman else None
    return article_no, item_no


def _find_article_by_id(data: dict[str, Any], article_id: str) -> Optional[dict[str, Any]]:
    for a in data.get("articles", []):
        if a.get("id") == article_id:
            return a
    return None


def _option(text: str, value: str) -> dict[str, Any]:
    return {"text": {"type": "plain_text", "text": text, "emoji": True}, "value": value}


def _initial_option(options: list[dict[str, Any]], value: Optional[str]) -> Optional[dict[str, Any]]:
    if value is None:
        return None
    for o in options:
        if o.get("value") == value:
            return o
    return None


def _slack_date(origin_ts: str) -> str:
    """
    Slackの date 置換を使って人間可読表示にする。
    https://docs.slack.dev/messaging/formatting-message-text/
    """
    try:
        unix_ts = int(float(origin_ts))
        return f"<!date^{unix_ts}^{{date_short_pretty}} {{time}}|{origin_ts}>"
    except Exception:
        return origin_ts  # フォールバック


def build_private_alert_blocks(
    *,
    reason: str,
    trace_id: str,
    origin_channel: str,
    origin_ts: str,
    user_id: str,
    approve_value: str,
    dismiss_value: str,
    default_article_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """
    Slack: chat.postMessage の blocks を返す
    - approve_value / dismiss_value は encode_alert_button_value(...) の結果（JSON文字列）
    - default_article_id があれば articles.json から regulation/article/item を引いて initial_option を付ける
    """
    data = _load_articles()

    # 1) 規約 options
    regs = (
        data.get("metadata", {}).get("regulations")
        or sorted({a.get("regulation") for a in data.get("articles", []) if a.get("regulation")})
    )
    regulation_options = [_option(r, r) for r in regs if r]

    # 2) 条 options
    article_nos = set()
    for a in data.get("articles", []):
        an, _ = _parse_article_id(a.get("id", ""))
        if an is not None:
            article_nos.add(an)
    article_options = [_option(f"第{i}条", str(i)) for i in range(1,15)]
    article_options.append(_option("（特則/条なし）", "special"))

    # 3) 項 options
    item_nos = set()
    for a in data.get("articles", []):
        _, in_ = _parse_article_id(a.get("id", ""))
        if in_ is not None:
            item_nos.add(in_)
    item_options = [_option(f"第{i}項", str(i)) for i in range(1, 7)]
    item_options.append(_option("（項なし）", "0"))

    # デフォルト値（article_id → regulation + 条 + 項）
    default_reg = None
    default_article_no = None
    default_item_no = None

    if default_article_id:
        hit = _find_article_by_id(data, default_article_id)
        if hit:
            default_reg = hit.get("regulation")
        an, in_ = _parse_article_id(default_article_id)
        default_article_no = str(an) if an is not None else "special"
        default_item_no = str(in_) if in_ is not None else "0"

    reg_init = _initial_option(regulation_options, default_reg)
    art_init = _initial_option(article_options, default_article_no)
    item_init = _initial_option(item_options, default_item_no)

    policy_select_block: dict[str, Any] = {
        "type": "actions",
        "block_id": POLICY_BLOCK_ID,
        "elements": [
            {
                "type": "static_select",
                "action_id": AID_REGULATION,
                "placeholder": {"type": "plain_text", "text": "規約（ガイドライン）", "emoji": True},
                "options": regulation_options,
                **({"initial_option": reg_init} if reg_init else {}),
            },
            {
                "type": "static_select",
                "action_id": AID_ARTICLE,
                "placeholder": {"type": "plain_text", "text": "第x条", "emoji": True},
                "options": article_options,
                **({"initial_option": art_init} if art_init else {}),
            },
            {
                "type": "static_select",
                "action_id": AID_ITEM,
                "placeholder": {"type": "plain_text", "text": "第y項", "emoji": True},
                "options": item_options,
                **({"initial_option": item_init} if item_init else {}),
            },
        ],
    }

    buttons_block = {
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "削除勧告を送る", "emoji": True},
                "style": "danger",
                "action_id": "approve_violation",
                "value": approve_value,
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "違反ではない（却下）", "emoji": True},
                "style": "primary",
                "action_id": "dismiss_violation",
                "value": dismiss_value,
            },
        ],
    }

    posted_at = _slack_date(origin_ts)

    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    "🚨 *違反の可能性がある投稿を検出しました*\n"
                    f"・理由: `{reason}`"
                ),
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f"チャンネル: <#{origin_channel}>   "
                        f"投稿者: <@{user_id}>   "
                        f"投稿日時: {posted_at}"
                    ),
                }
            ],
        },
        {"type": "divider"},
        policy_select_block,
        buttons_block,
    ]
    return blocks
