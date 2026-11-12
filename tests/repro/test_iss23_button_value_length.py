"""
ISS-23: ボタンの value に判定理由を全文入れており、長いと Slack の 2,000 文字の上限を超えうる
docs/requirements/issues.md の該当の節: ## 詳細 > ### ISS-23 ボタンの value の長さ

第 1 版の再現は切り詰めを通らない呼び方で作っていた。実際の経路では判定理由だけでは 2,000 文字を超えない。
今の動きを固定する特性テスト。不具合を直したら期待値を書き換えること。
"""

from unittest.mock import MagicMock
import pytest
from app_inspect.components.slack_notifier import send_new_violation_alert
from app_inspect.services.models import ModerationResult, SlackIdentity
from app_inspect.services.moderation import run_moderation
from app_inspect.services.violation_detector import DetectionResult


def _find_first_button(blocks: list) -> dict:
    """Slack blocks から最初の button element を取得する。"""
    for b in blocks:
        if b.get("type") == "actions":
            for el in b.get("elements", []):
                if el.get("type") == "button":
                    return el
    raise AssertionError("No button element found in blocks")


def test_button_value_length_within_limit_on_actual_path(mocker):
    """実際の経路: run_moderation を呼ぶと ViolationDetector の理由が 800 文字で切られ、ボタンの value が 2,000 文字以下となることを確認。"""
    mock_detector_cls = mocker.patch("app_inspect.services.moderation.ViolationDetector")
    mock_detector_inst = mock_detector_cls.return_value
    mock_detector_inst.detect.return_value = DetectionResult(
        is_violation=True,
        confidence=0.9,
        method="LLM",
        article_id="11-v",
        category="勧誘行為",
        reason="あ" * 1950,
    )

    mock_client = MagicMock()
    mod_result = run_moderation(mock_client, "gpt-4o-mini", "テスト投稿")

    assert len(mod_result.rationale) == 800

    mock_slack = MagicMock()
    mock_slack.chat_postMessage.return_value = {"ok": True, "ts": "1700000000.999999"}

    mock_context = MagicMock()
    mock_context.trace_id = "test-trace-id-12345"

    mock_cfg = MagicMock()
    mock_cfg.alert_private_channel_id = "C_ADMIN"
    mock_cfg.use_mock_openai = False
    mock_cfg.notion_template_db_id = None

    identity = SlackIdentity(
        profile_name="test_user",
        channel_name="general",
        workspace_name="workspace",
    )

    send_new_violation_alert(
        context=mock_context,
        slack_client=mock_slack,
        cfg=mock_cfg,
        trace_id=mock_context.trace_id,
        identity=identity,
        raw_user_id="U_TEST",
        raw_channel_id="C_TEST",
        message_ts="1700000000.000100",
        post_text="テスト投稿本文",
        post_link="https://slack.com/archives/C_TEST/p1700000000000100",
        notion_page_id="page-12345",
        article_display_name="受講規約第11条(v)",
        result=mod_result,
    )

    mock_slack.chat_postMessage.assert_called_once()
    _, kwargs = mock_slack.chat_postMessage.call_args
    blocks = kwargs["blocks"]

    btn = _find_first_button(blocks)
    button_value = btn["value"]
    assert len(button_value) <= 2000


def test_button_value_length_exceeds_2000_when_rationale_is_long():
    """対照テスト: send_new_violation_alert 自身には長さの制限が無い。1,950 文字の rationale を持つ ModerationResult を直接渡すと value が 2,000 文字を超える。
    （実際の経路では起きない。normalize_result の切り詰めを外すと超える）
    """
    mock_slack = MagicMock()
    mock_slack.chat_postMessage.return_value = {"ok": True, "ts": "1700000000.999999"}

    mock_context = MagicMock()
    mock_context.trace_id = "test-trace-id-12345"

    mock_cfg = MagicMock()
    mock_cfg.alert_private_channel_id = "C_ADMIN"
    mock_cfg.use_mock_openai = False
    mock_cfg.notion_template_db_id = None

    identity = SlackIdentity(
        profile_name="test_user",
        channel_name="general",
        workspace_name="workspace",
    )

    long_rationale = "あ" * 1950
    result = ModerationResult(
        is_violation=True,
        severity="high",
        categories=["勧誘行為"],
        rationale=long_rationale,
        recommended_reply="削除を推奨します",
        confidence=0.9,
        article_id="11-v",
        method="LLM",
    )

    send_new_violation_alert(
        context=mock_context,
        slack_client=mock_slack,
        cfg=mock_cfg,
        trace_id=mock_context.trace_id,
        identity=identity,
        raw_user_id="U_TEST",
        raw_channel_id="C_TEST",
        message_ts="1700000000.000100",
        post_text="テスト投稿本文",
        post_link="https://slack.com/archives/C_TEST/p1700000000000100",
        notion_page_id="page-12345",
        article_display_name="受講規約第11条(v)",
        result=result,
    )

    mock_slack.chat_postMessage.assert_called_once()
    _, kwargs = mock_slack.chat_postMessage.call_args
    blocks = kwargs["blocks"]

    btn = _find_first_button(blocks)
    button_value = btn["value"]
    assert len(button_value) > 2000
