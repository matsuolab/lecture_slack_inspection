import json
from unittest.mock import MagicMock
import pytest
import requests

from app_inspect.services.violation_detector import (
    DECISIONS_API_URL,
    DECISIONS_MODEL,
    DECISIONS_TIMEOUT_SECONDS,
    ViolationDetector,
    _DECISION_TEXT_PLACEHOLDER,
)


@pytest.fixture
def tmp_articles_file(tmp_path):
    articles_data = {
        "articles": [
            {
                "id": "11-v",
                "article": "第11条(v)",
                "content": "宗教・マルチ商法等の勧誘行為",
                "category": "勧誘行為",
            },
            {
                "id": "11-xiii",
                "article": "第11条(xiii)",
                "content": "外部コミュニティへの勧誘や宣伝広告",
                "category": "宣伝・勧誘",
            },
            {
                "id": "11-xvi",
                "article": "第11条(xvi)",
                "content": "その他不適切な行為",
                "category": "不適切行為",
            },
        ]
    }
    file_path = tmp_path / "test_articles.json"
    file_path.write_text(json.dumps(articles_data, ensure_ascii=False), encoding="utf-8")
    return str(file_path)


@pytest.fixture
def mock_openai_client():
    client = MagicMock()
    client.api_key = "sk-test"
    return client


# 1. 既定 (llm_score) は requests.post を呼ばず既存経路
def test_default_mode_uses_llm_score(mocker, tmp_articles_file, mock_openai_client, monkeypatch):
    monkeypatch.delenv("DETECTION_MODE", raising=False)
    mock_post = mocker.patch("app_inspect.services.violation_detector.requests.post")
    mock_openai_client.chat.completions.create.return_value = MagicMock(
        choices=[
            MagicMock(
                message=MagicMock(
                    content=json.dumps(
                        {"violation_score": 60, "confidence": 0.8, "article_id": "11-v", "reason": "違反"}
                    )
                )
            )
        ]
    )

    detector = ViolationDetector(
        openai_client=mock_openai_client,
        judge_model="gpt-4o-mini",
        articles_path=tmp_articles_file,
    )
    result = detector.detect("テスト投稿")

    mock_post.assert_not_called()
    mock_openai_client.chat.completions.create.assert_called_once()
    call_kwargs = mock_openai_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "gpt-4o-mini"
    assert call_kwargs["temperature"] == 0
    assert result.is_violation is True
    assert result.method == "LLM"
    assert result.article_id == "11-v"
    assert result.reason.startswith("[11-v 第11条(v)]")


# 2. decisions: warn p=0.9、article の確率が 11-xiii 0.6 / 11-v 0.3 / 11-xvi 0.1
#    → 2 段目に 3 候補が確率順で渡り、2 段目の答え (例: 11-v) が article_id になる。
#    method "Decisions"、confidence 0.9、candidates の順序
def test_decisions_mode_warn_stage2_choice(mocker, tmp_articles_file, mock_openai_client, monkeypatch):
    monkeypatch.setenv("DETECTION_MODE", "decisions")
    mock_post = mocker.patch("app_inspect.services.violation_detector.requests.post")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "answers": [
            {
                "name": "verdict",
                "type": "choice",
                "probabilities": [
                    {"value": "warn", "probability": 0.9},
                    {"value": "safe", "probability": 0.1},
                ],
            },
            {
                "name": "article",
                "type": "choice",
                "probabilities": [
                    {"value": "11-xiii", "probability": 0.6},
                    {"value": "11-v", "probability": 0.3},
                    {"value": "11-xvi", "probability": 0.1},
                ],
            },
        ]
    }
    mock_post.return_value = mock_resp

    mock_openai_client.chat.completions.create.return_value = MagicMock(
        choices=[
            MagicMock(
                message=MagicMock(
                    content=json.dumps(
                        {"article_id": "11-v", "reason": "宗教勧誘に該当"}
                    )
                )
            )
        ]
    )

    detector = ViolationDetector(
        openai_client=mock_openai_client,
        judge_model="gpt-6-luna",
        articles_path=tmp_articles_file,
    )
    result = detector.detect("テスト投稿")

    assert result.is_violation is True
    assert result.confidence == 0.9
    assert result.method == "Decisions"
    assert result.article_id == "11-v"
    assert result.category == "勧誘行為"
    assert "[11-v 第11条(v)] 宗教勧誘に該当" in result.reason

    # 2段目の呼び出し引数検証
    mock_openai_client.chat.completions.create.assert_called_once()
    call_kwargs = mock_openai_client.chat.completions.create.call_args.kwargs
    assert "temperature" not in call_kwargs
    schema = call_kwargs["response_format"]["json_schema"]["schema"]
    # 確率順に渡されていること
    assert schema["properties"]["article_id"]["enum"] == ["11-xiii", "11-v", "11-xvi"]
    stage2_prompt = call_kwargs["messages"][0]["content"]
    assert "- 11-xiii 第11条(xiii) [宣伝・勧誘]: 外部コミュニティへの勧誘や宣伝広告" in stage2_prompt
    assert "- 11-v 第11条(v) [勧誘行為]: 宗教・マルチ商法等の勧誘行為" in stage2_prompt
    assert "- 11-xvi 第11条(xvi) [不適切行為]: その他不適切な行為" in stage2_prompt


# 3. decisions: article の choices に none が無い。verdict の instructions は前回と同じ組み立て
def test_decisions_request_payload_choices_and_instructions(
    mocker, tmp_articles_file, mock_openai_client, monkeypatch
):
    monkeypatch.setenv("DETECTION_MODE", "decisions")
    mock_post = mocker.patch("app_inspect.services.violation_detector.requests.post")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "answers": [
            {
                "name": "verdict",
                "type": "choice",
                "probabilities": [{"value": "safe", "probability": 0.8}, {"value": "warn", "probability": 0.2}],
            },
            {
                "name": "article",
                "type": "choice",
                "probabilities": [
                    {"value": "11-v", "probability": 0.5},
                    {"value": "11-xiii", "probability": 0.3},
                    {"value": "11-xvi", "probability": 0.2},
                ],
            },
        ]
    }
    mock_post.return_value = mock_resp

    detector = ViolationDetector(
        openai_client=mock_openai_client,
        judge_model="gpt-6-luna",
        articles_path=tmp_articles_file,
    )
    detector.detect("投稿本文")

    mock_post.assert_called_once()
    call_args, call_kwargs = mock_post.call_args
    assert call_args[0] == DECISIONS_API_URL
    assert call_kwargs["timeout"] == DECISIONS_TIMEOUT_SECONDS
    assert call_kwargs["headers"]["Authorization"] == "Bearer sk-test"

    payload = call_kwargs["json"]
    assert payload["model"] == DECISIONS_MODEL
    assert payload["input"] == "## 投稿内容\n```\n投稿本文\n```"

    questions = payload["questions"]
    assert len(questions) == 2
    verdict_q = next(q for q in questions if q["name"] == "verdict")
    article_q = next(q for q in questions if q["name"] == "article")

    assert "- 11-v 第11条(v): 宗教・マルチ商法等の勧誘行為" in verdict_q["instructions"]
    assert verdict_q["instructions"].endswith(
        "\n運営がこの投稿者に警告を送るべきなら warn、そうでなければ safe を選ぶ。"
    )
    assert verdict_q["choices"] == [{"value": "warn"}, {"value": "safe"}]

    assert "この投稿が警告の対象だとしたら、最も当てはまる条文を 1 つ選ぶ。" in article_q["instructions"]
    assert "## 条文\n" in article_q["instructions"]
    assert "- 11-v 第11条(v): 宗教・マルチ商法等の勧誘行為" in article_q["instructions"]

    choice_values = [c["value"] for c in article_q["choices"]]
    assert "none" not in choice_values
    assert choice_values == ["11-v", "11-xiii", "11-xvi"]


# 4. decisions: safe (p=0.2) → 2 段目を呼ばない、article_id None
def test_decisions_mode_safe_no_stage2(mocker, tmp_articles_file, mock_openai_client, monkeypatch):
    monkeypatch.setenv("DETECTION_MODE", "decisions")
    mock_post = mocker.patch("app_inspect.services.violation_detector.requests.post")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "answers": [
            {
                "name": "verdict",
                "type": "choice",
                "probabilities": [
                    {"value": "warn", "probability": 0.2},
                    {"value": "safe", "probability": 0.8},
                ],
            },
            {
                "name": "article",
                "type": "choice",
                "probabilities": [
                    {"value": "11-v", "probability": 0.4},
                    {"value": "11-xiii", "probability": 0.3},
                    {"value": "11-xvi", "probability": 0.3},
                ],
            },
        ]
    }
    mock_post.return_value = mock_resp

    detector = ViolationDetector(
        openai_client=mock_openai_client,
        judge_model="gpt-6-luna",
        articles_path=tmp_articles_file,
    )
    result = detector.detect("テスト投稿")

    mock_openai_client.chat.completions.create.assert_not_called()
    assert result.is_violation is False
    assert result.confidence == 0.2
    assert result.article_id is None
    assert result.method == "Decisions"
    assert "Decisions API: P(warn)=0.20" in result.reason


# 5. decisions: 2 段目が例外 → 候補の 1 位が article_id、reason に P(warn)=0.90
def test_decisions_mode_stage2_exception_fallback(mocker, tmp_articles_file, mock_openai_client, monkeypatch):
    monkeypatch.setenv("DETECTION_MODE", "decisions")
    mock_post = mocker.patch("app_inspect.services.violation_detector.requests.post")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "answers": [
            {
                "name": "verdict",
                "type": "choice",
                "probabilities": [
                    {"value": "warn", "probability": 0.9},
                    {"value": "safe", "probability": 0.1},
                ],
            },
            {
                "name": "article",
                "type": "choice",
                "probabilities": [
                    {"value": "11-xiii", "probability": 0.6},
                    {"value": "11-v", "probability": 0.3},
                    {"value": "11-xvi", "probability": 0.1},
                ],
            },
        ]
    }
    mock_post.return_value = mock_resp

    mock_openai_client.chat.completions.create.side_effect = RuntimeError("Stage 2 failure")

    detector = ViolationDetector(
        openai_client=mock_openai_client,
        judge_model="gpt-6-luna",
        articles_path=tmp_articles_file,
    )
    result = detector.detect("テスト投稿")

    assert result.is_violation is True
    assert result.confidence == 0.9
    assert result.method == "Decisions"
    assert result.article_id == "11-xiii"
    assert result.category == "宣伝・勧誘"
    assert "[11-xiii 第11条(xiii)] Decisions API: P(warn)=0.90" in result.reason


# 6. decisions: Decisions API が例外 → 経路 B に切り替わる (method "LLM-fallback")
def test_decisions_mode_api_exception_triggers_fallback(
    mocker, tmp_articles_file, mock_openai_client, monkeypatch
):
    monkeypatch.setenv("DETECTION_MODE", "decisions")
    mocker.patch(
        "app_inspect.services.violation_detector.requests.post",
        side_effect=requests.HTTPError("500 Server Error"),
    )

    mock_openai_client.chat.completions.create.side_effect = [
        MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(
                        content=json.dumps(
                            {"decision": "warn", "candidates": ["11-v", "11-xiii", "11-xvi"], "reason": "外部勧誘の疑い"}
                        )
                    )
                )
            ]
        ),
        MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(
                        content=json.dumps(
                            {"article_id": "11-xiii", "reason": "外部グループ告知"}
                        )
                    )
                )
            ]
        ),
    ]

    detector = ViolationDetector(
        openai_client=mock_openai_client,
        judge_model="gpt-6-luna",
        articles_path=tmp_articles_file,
    )
    result = detector.detect("テスト投稿")

    assert result.is_violation is True
    assert result.confidence == 0.8
    assert result.method == "LLM-fallback"
    assert result.article_id == "11-xiii"
    assert result.category == "宣伝・勧誘"
    assert "[11-xiii 第11条(xiii)] [luna] 外部グループ告知" in result.reason


# 7. luna: requests.post を呼ばない。1 段目の prompt に投稿本文と article_guide の文面と出力指定が入り、
#    (input の投稿) が残っていない。warn → 2 段目 → method "Luna"、reason が [luna] を含む
def test_luna_mode_warn_pipeline(mocker, tmp_articles_file, mock_openai_client, monkeypatch):
    monkeypatch.setenv("DETECTION_MODE", "luna")
    mock_post = mocker.patch("app_inspect.services.violation_detector.requests.post")

    mock_openai_client.chat.completions.create.side_effect = [
        MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(
                        content=json.dumps(
                            {"decision": "warn", "candidates": ["11-v", "11-xiii"], "reason": "勧誘疑い"}
                        )
                    )
                )
            ]
        ),
        MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(
                        content=json.dumps(
                            {"article_id": "11-v", "reason": "マルチ勧誘行為"}
                        )
                    )
                )
            ]
        ),
    ]

    detector = ViolationDetector(
        openai_client=mock_openai_client,
        judge_model="gpt-6-luna",
        articles_path=tmp_articles_file,
    )
    result = detector.detect("受講生のテスト投稿")

    mock_post.assert_not_called()
    assert mock_openai_client.chat.completions.create.call_count == 2

    call1_kwargs = mock_openai_client.chat.completions.create.call_args_list[0].kwargs
    assert "temperature" not in call1_kwargs
    prompt1 = call1_kwargs["messages"][0]["content"]
    assert "受講生のテスト投稿" in prompt1
    assert _DECISION_TEXT_PLACEHOLDER not in prompt1
    assert "(input の投稿)" not in prompt1
    assert "## 条文の選び方 (運営の方針と条文の対応)" in prompt1
    assert "candidates: この投稿が警告の対象だとしたら当てはまりそうな条文 ID を、当てはまる順に 3 つ" in prompt1

    assert result.is_violation is True
    assert result.confidence == 0.8
    assert result.method == "Luna"
    assert result.article_id == "11-v"
    assert result.category == "勧誘行為"
    assert "[11-v 第11条(v)] [luna] マルチ勧誘行為" in result.reason


# 8. luna: 候補に条文一覧に無い ID や重複がある → 除かれる。全部無効なら 11-xvi
def test_luna_mode_candidate_sanitization(mocker, tmp_articles_file, mock_openai_client, monkeypatch):
    monkeypatch.setenv("DETECTION_MODE", "luna")
    mock_post = mocker.patch("app_inspect.services.violation_detector.requests.post")

    mock_openai_client.chat.completions.create.side_effect = [
        MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(
                        content=json.dumps(
                            {"decision": "warn", "candidates": ["unknown-1", "11-v", "11-v", "11-xiii"], "reason": "テスト"}
                        )
                    )
                )
            ]
        ),
        MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(
                        content=json.dumps(
                            {"article_id": "11-v", "reason": "理由"}
                        )
                    )
                )
            ]
        ),
    ]

    detector = ViolationDetector(
        openai_client=mock_openai_client,
        judge_model="gpt-6-luna",
        articles_path=tmp_articles_file,
    )
    result = detector.detect("テスト投稿1")

    call2_kwargs = mock_openai_client.chat.completions.create.call_args_list[1].kwargs
    schema = call2_kwargs["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["article_id"]["enum"] == ["11-v", "11-xiii"]
    assert result.article_id == "11-v"

    # 全部無効な候補の場合 → 11-xvi にフォールバック
    mock_openai_client.chat.completions.create.reset_mock()
    mock_openai_client.chat.completions.create.side_effect = [
        MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(
                        content=json.dumps(
                            {"decision": "warn", "candidates": ["invalid-1", "invalid-2"], "reason": "テスト2"}
                        )
                    )
                )
            ]
        ),
        MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(
                        content=json.dumps(
                            {"article_id": "11-xvi", "reason": "不適切行為"}
                        )
                    )
                )
            ]
        ),
    ]

    result2 = detector.detect("テスト投稿2")
    call2_kwargs2 = mock_openai_client.chat.completions.create.call_args_list[1].kwargs
    schema2 = call2_kwargs2["response_format"]["json_schema"]["schema"]
    assert schema2["properties"]["article_id"]["enum"] == ["11-xvi"]
    assert result2.article_id == "11-xvi"


# 9. luna: 1 段目が例外 → is_violation False、reason が "LLM判定エラー" で始まる
def test_luna_mode_stage1_exception_returns_error_result(
    mocker, tmp_articles_file, mock_openai_client, monkeypatch
):
    monkeypatch.setenv("DETECTION_MODE", "luna")
    mock_openai_client.chat.completions.create.side_effect = RuntimeError("Stage 1 LLM failure")

    detector = ViolationDetector(
        openai_client=mock_openai_client,
        judge_model="gpt-6-luna",
        articles_path=tmp_articles_file,
    )
    result = detector.detect("テスト投稿")

    assert result.is_violation is False
    assert result.confidence == 0.0
    assert result.article_id is None
    assert result.method == "Luna"
    assert result.reason.startswith("LLM判定エラー: Stage 1 LLM failure")


# 10. 2 段目の prompt で、投稿本文に {{article_guide}} という文字列があっても展開されない
def test_stage2_prompt_preserves_template_variable_in_text(
    mocker, tmp_articles_file, mock_openai_client, monkeypatch
):
    monkeypatch.setenv("DETECTION_MODE", "luna")
    mock_openai_client.chat.completions.create.side_effect = [
        MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(
                        content=json.dumps(
                            {"decision": "warn", "candidates": ["11-v"], "reason": "疑い"}
                        )
                    )
                )
            ]
        ),
        MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(
                        content=json.dumps(
                            {"article_id": "11-v", "reason": "違反理由"}
                        )
                    )
                )
            ]
        ),
    ]

    detector = ViolationDetector(
        openai_client=mock_openai_client,
        judge_model="gpt-6-luna",
        articles_path=tmp_articles_file,
    )
    text_with_template_tag = "受講生の質問: {{article_guide}} と {{candidates_text}} の関係を教えて"
    detector.detect(text_with_template_tag)

    call2_kwargs = mock_openai_client.chat.completions.create.call_args_list[1].kwargs
    prompt2 = call2_kwargs["messages"][0]["content"]
    assert text_with_template_tag in prompt2


# 境界値・モード設定テスト
def test_detection_mode_argument_and_unknown_mode(tmp_articles_file, mock_openai_client, monkeypatch):
    monkeypatch.delenv("DETECTION_MODE", raising=False)

    detector_decisions = ViolationDetector(
        openai_client=mock_openai_client,
        judge_model="gpt-6-luna",
        articles_path=tmp_articles_file,
        detection_mode="decisions",
    )
    assert detector_decisions.detection_mode == "decisions"

    detector_luna = ViolationDetector(
        openai_client=mock_openai_client,
        judge_model="gpt-6-luna",
        articles_path=tmp_articles_file,
        detection_mode="luna",
    )
    assert detector_luna.detection_mode == "luna"

    detector_unknown = ViolationDetector(
        openai_client=mock_openai_client,
        judge_model="gpt-6-luna",
        articles_path=tmp_articles_file,
        detection_mode="unknown_mode",
    )
    assert detector_unknown.detection_mode == "llm_score"

    monkeypatch.setenv("DETECTION_MODE", "invalid_env_mode")
    detector_env = ViolationDetector(
        openai_client=mock_openai_client,
        judge_model="gpt-6-luna",
        articles_path=tmp_articles_file,
    )
    assert detector_env.detection_mode == "llm_score"


def test_decisions_mode_boundary_threshold(mocker, tmp_articles_file, mock_openai_client, monkeypatch):
    monkeypatch.setenv("DETECTION_MODE", "decisions")
    mock_post = mocker.patch("app_inspect.services.violation_detector.requests.post")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "answers": [
            {
                "name": "verdict",
                "type": "choice",
                "probabilities": [
                    {"value": "warn", "probability": 0.5},
                    {"value": "safe", "probability": 0.5},
                ],
            },
            {
                "name": "article",
                "type": "choice",
                "probabilities": [
                    {"value": "11-v", "probability": 0.5},
                    {"value": "11-xiii", "probability": 0.3},
                    {"value": "11-xvi", "probability": 0.2},
                ],
            },
        ]
    }
    mock_post.return_value = mock_resp

    mock_openai_client.chat.completions.create.return_value = MagicMock(
        choices=[
            MagicMock(
                message=MagicMock(
                    content=json.dumps(
                        {"article_id": "11-v", "reason": "境界値"}
                    )
                )
            )
        ]
    )

    detector = ViolationDetector(
        openai_client=mock_openai_client,
        judge_model="gpt-6-luna",
        articles_path=tmp_articles_file,
    )
    result = detector.detect("テスト投稿")

    assert result.is_violation is True
    assert result.confidence == 0.5
    assert result.article_id == "11-v"


def test_luna_mode_safe_no_stage2(mocker, tmp_articles_file, mock_openai_client, monkeypatch):
    monkeypatch.setenv("DETECTION_MODE", "luna")
    mock_openai_client.chat.completions.create.return_value = MagicMock(
        choices=[
            MagicMock(
                message=MagicMock(
                    content=json.dumps(
                        {"decision": "safe", "candidates": ["11-v", "11-xiii", "11-xvi"], "reason": "安全な挨拶"}
                    )
                )
            )
        ]
    )

    detector = ViolationDetector(
        openai_client=mock_openai_client,
        judge_model="gpt-6-luna",
        articles_path=tmp_articles_file,
    )
    result = detector.detect("こんにちは！よろしくお願いします。")

    assert mock_openai_client.chat.completions.create.call_count == 1
    assert result.is_violation is False
    assert result.confidence == 0.2
    assert result.article_id is None
    assert result.method == "Luna"
    assert result.reason == "[luna] 安全な挨拶"


def test_luna_mode_stage2_exception_fallback(mocker, tmp_articles_file, mock_openai_client, monkeypatch):
    monkeypatch.setenv("DETECTION_MODE", "luna")
    mock_openai_client.chat.completions.create.side_effect = [
        MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(
                        content=json.dumps(
                            {"decision": "warn", "candidates": ["11-xiii", "11-v"], "reason": "外部宣伝の疑い"}
                        )
                    )
                )
            ]
        ),
        RuntimeError("Stage 2 failure"),
    ]

    detector = ViolationDetector(
        openai_client=mock_openai_client,
        judge_model="gpt-6-luna",
        articles_path=tmp_articles_file,
    )
    result = detector.detect("テスト投稿")

    assert result.is_violation is True
    assert result.confidence == 0.8
    assert result.method == "Luna"
    assert result.article_id == "11-xiii"
    assert result.category == "宣伝・勧誘"
    assert "[11-xiii 第11条(xiii)] [luna] 外部宣伝の疑い" in result.reason


# llm_score モードでの temperature フォールバックテスト
def test_llm_score_mode_success_with_temperature_zero(tmp_articles_file, mock_openai_client, monkeypatch):
    monkeypatch.setenv("DETECTION_MODE", "llm_score")
    mock_openai_client.chat.completions.create.return_value = MagicMock(
        choices=[
            MagicMock(
                message=MagicMock(
                    content=json.dumps(
                        {"violation_score": 60, "confidence": 0.8, "article_id": "11-v", "reason": "違反"}
                    )
                )
            )
        ]
    )

    detector = ViolationDetector(
        openai_client=mock_openai_client,
        judge_model="gpt-4o-mini",
        articles_path=tmp_articles_file,
    )
    result = detector.detect("テスト投稿")

    assert mock_openai_client.chat.completions.create.call_count == 1
    call_kwargs = mock_openai_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["temperature"] == 0
    assert call_kwargs["model"] == "gpt-4o-mini"
    assert result.is_violation is True


def test_llm_score_mode_retries_without_temperature_when_unsupported(
    tmp_articles_file, mock_openai_client, monkeypatch
):
    monkeypatch.setenv("DETECTION_MODE", "llm_score")
    mock_openai_client.chat.completions.create.side_effect = [
        Exception(
            "Unsupported value: 'temperature' does not support 0 with this model. Only the default (1) value is supported."
        ),
        MagicMock(
            choices=[
                MagicMock(
                    message=MagicMock(
                        content=json.dumps(
                            {"violation_score": 80, "confidence": 0.9, "article_id": "11-v", "reason": "違反"}
                        )
                    )
                )
            ]
        ),
    ]

    detector = ViolationDetector(
        openai_client=mock_openai_client,
        judge_model="gpt-6-luna",
        articles_path=tmp_articles_file,
    )
    result = detector.detect("テスト投稿")

    assert mock_openai_client.chat.completions.create.call_count == 2
    call1_kwargs = mock_openai_client.chat.completions.create.call_args_list[0].kwargs
    call2_kwargs = mock_openai_client.chat.completions.create.call_args_list[1].kwargs

    assert call1_kwargs["temperature"] == 0
    assert "temperature" not in call2_kwargs
    assert call2_kwargs["model"] == call1_kwargs["model"]
    assert call2_kwargs["messages"] == call1_kwargs["messages"]
    assert call2_kwargs["response_format"] == call1_kwargs["response_format"]
    assert result.is_violation is True


def test_llm_score_mode_unrelated_exception_does_not_retry(
    tmp_articles_file, mock_openai_client, monkeypatch
):
    monkeypatch.setenv("DETECTION_MODE", "llm_score")
    mock_openai_client.chat.completions.create.side_effect = Exception("rate limit")

    detector = ViolationDetector(
        openai_client=mock_openai_client,
        judge_model="gpt-4o-mini",
        articles_path=tmp_articles_file,
    )
    result = detector.detect("テスト投稿")

    assert mock_openai_client.chat.completions.create.call_count == 1
    assert result.is_violation is False
    assert result.reason.startswith("LLM判定エラー")


def test_llm_score_mode_temperature_exception_and_retry_also_fails(
    tmp_articles_file, mock_openai_client, monkeypatch
):
    monkeypatch.setenv("DETECTION_MODE", "llm_score")
    mock_openai_client.chat.completions.create.side_effect = [
        Exception(
            "Unsupported value: 'temperature' does not support 0 with this model. Only the default (1) value is supported."
        ),
        Exception("server error"),
    ]

    detector = ViolationDetector(
        openai_client=mock_openai_client,
        judge_model="gpt-6-luna",
        articles_path=tmp_articles_file,
    )
    result = detector.detect("テスト投稿")

    assert mock_openai_client.chat.completions.create.call_count == 2
    assert result.is_violation is False
    assert result.reason.startswith("LLM判定エラー")
