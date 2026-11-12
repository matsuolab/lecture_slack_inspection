"""
ISS-01: 違反かどうかを決める段の失敗が「違反なし」になる
docs/requirements/issues.md の該当の節: ## 詳細 > ### ISS-01 違反かどうかを決める段の失敗が「違反なし」になる

今の動きを固定する特性テスト。不具合を直したら期待値を書き換えること。
"""

import json
from unittest.mock import MagicMock
import pytest
import requests

from app_inspect.handler import _build_moderation_executor
from app_inspect.services.moderation import run_moderation
from app_inspect.services.violation_detector import ViolationDetector
from common.observability import build_context


def test_llm_score_exception_returns_non_violation():
    """llm_score モードで chat.completions.create が例外を投げた場合、detect() の結果が is_violation=False となることを確認。"""
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = RuntimeError("OpenAI API error")

    detector = ViolationDetector(
        openai_client=mock_client,
        judge_model="gpt-4o-mini",
        detection_mode="llm_score",
    )
    result = detector.detect("テスト投稿")

    assert result.is_violation is False
    assert result.confidence == 0.0


def test_luna_stage1_exception_returns_non_violation():
    """luna モードで 1 段目（chat.completions.create）が例外を投げた場合、detect() の結果が is_violation=False となることを確認。"""
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = RuntimeError("Stage 1 LLM failure")

    detector = ViolationDetector(
        openai_client=mock_client,
        judge_model="gpt-6-luna",
        detection_mode="luna",
    )
    result = detector.detect("テスト投稿")

    assert result.is_violation is False
    assert result.confidence == 0.0


def test_decisions_both_paths_exception_returns_non_violation(mocker):
    """decisions モードで Decisions API と予備経路（chat.completions.create）の両方が例外を投げた場合、is_violation=False となることを確認。"""
    mock_client = MagicMock()
    mock_client.api_key = "sk-test"

    mocker.patch(
        "app_inspect.services.violation_detector.requests.post",
        side_effect=requests.RequestException("Decisions API failure"),
    )
    mock_client.chat.completions.create.side_effect = RuntimeError("Fallback LLM error")

    detector = ViolationDetector(
        openai_client=mock_client,
        judge_model="gpt-6-luna",
        detection_mode="decisions",
    )
    result = detector.detect("テスト投稿")

    assert result.is_violation is False
    assert result.confidence == 0.0


def test_run_moderation_detector_exception_returns_non_violation(mocker):
    """run_moderation 内で ViolationDetector.detect が例外を投げた場合、is_violation=False、method=None が返ることを確認。"""
    mock_detector_cls = mocker.patch("app_inspect.services.moderation.ViolationDetector")
    mock_detector_inst = mock_detector_cls.return_value
    mock_detector_inst.detect.side_effect = RuntimeError("Detector crash")

    mock_client = MagicMock()
    result = run_moderation(mock_client, "gpt-4o-mini", "テスト投稿")

    assert result.is_violation is False
    assert result.method is None


def test_contrast_stage2_failure_preserves_violation():
    """対照テスト: luna モードで 2 段目（条文選択）のみが失敗した場合、is_violation=True が保たれ、候補 1 位の条文が選択されることを確認。"""
    mock_client = MagicMock()
    stage1_response = MagicMock()
    stage1_response.choices = [
        MagicMock(
            message=MagicMock(
                content=json.dumps({
                    "decision": "warn",
                    "candidates": ["11-v", "11-iv"],
                    "reason": "勧誘行為の疑い",
                })
            )
        )
    ]
    mock_client.chat.completions.create.side_effect = [
        stage1_response,
        RuntimeError("Stage 2 failure"),
    ]

    detector = ViolationDetector(
        openai_client=mock_client,
        judge_model="gpt-6-luna",
        detection_mode="luna",
    )
    result = detector.detect("テスト投稿")

    assert result.is_violation is True
    assert result.article_id == "11-v"


def test_moderation_executor_detection_failure_emits_latency_only_and_no_error_log(mocker):
    """moderation_executor 実行時に ViolationDetector が例外を投げても、メトリクスは InferenceLatencyMs のみ記録され、log_error は呼ばれないことを確認。"""
    mock_emit_metric = mocker.patch("app_inspect.handler.emit_metric")
    mock_log_error = mocker.patch("app_inspect.handler.log_error")

    mock_detector_cls = mocker.patch("app_inspect.services.moderation.ViolationDetector")
    mock_detector_inst = mock_detector_cls.return_value
    mock_detector_inst.detect.side_effect = RuntimeError("Detector crash")

    mock_context = build_context({}, None, "app_inspect")
    mock_cfg = MagicMock()
    mock_cfg.use_mock_openai = False
    mock_cfg.openai_api_key = "sk-test"
    mock_cfg.openai_model = "gpt-4o-mini"

    mock_notion = MagicMock()
    mock_notion.query_workspace_page_id.return_value = None

    moderate_fn = _build_moderation_executor(
        mock_context, mock_cfg, mock_notion, team_id="T_TEST"
    )
    result = moderate_fn("テスト投稿")

    assert result.is_violation is False
    assert mock_emit_metric.call_count == 1
    call_metric_name = mock_emit_metric.call_args[0][1]
    assert call_metric_name == "InferenceLatencyMs"
    mock_log_error.assert_not_called()
