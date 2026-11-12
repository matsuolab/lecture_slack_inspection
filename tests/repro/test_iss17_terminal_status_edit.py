"""
ISS-17: 状態の流れに抜けがある（未対応の放置、再警告済みのその後、対応終了の投稿が再び違反になったとき）
docs/requirements/issues.md の該当の節: ## 詳細 > ### ISS-17 状態の流れの抜け

今の動きを固定する特性テスト。不具合を直したら期待値を書き換えること。
"""

import pytest
from app_inspect.services.models import ModerationResult
from app_inspect.services.violation_transition import decide_edit_transition


@pytest.mark.parametrize("status", ["対応終了", "対応不要", "未対応"])
def test_edit_transition_with_terminal_or_unhandled_status_returns_reply_still_violation(status):
    """対応終了・対応不要・未対応のレコードがある投稿が編集後も違反だった場合、decide_edit_transition が reply_still_violation を返すことを確認。"""
    existing_page = {
        "id": "page-test-123",
        "properties": {
            "対応ステータス": {"select": {"name": status}},
        },
    }
    result = ModerationResult(
        is_violation=True,
        severity="high",
        categories=["迷惑行為"],
        rationale="編集後も依然として規約違反の内容が含まれています",
        recommended_reply="削除を推奨します",
        confidence=0.9,
        article_id="edu-3-ii",
        method="LLM",
    )

    decision = decide_edit_transition(
        existing_page=existing_page,
        result=result,
        min_severity_to_alert="medium",
    )

    assert decision.action == "reply_still_violation"
    assert decision.current_status == status
