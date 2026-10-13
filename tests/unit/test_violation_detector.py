import json
import logging
from unittest.mock import MagicMock
import pytest
import requests

from app_inspect.services.violation_detector import (
    DECISIONS_API_URL,
    DECISIONS_MODEL,
    DECISIONS_TIMEOUT_SECONDS,
    ViolationDetector,
    _DECISION_TEXT_PLACEHOLDER,
    _render_template,
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
    assert detector_unknown.detection_mode == "decisions"

    monkeypatch.setenv("DETECTION_MODE", "invalid_env_mode")
    detector_env = ViolationDetector(
        openai_client=mock_openai_client,
        judge_model="gpt-6-luna",
        articles_path=tmp_articles_file,
    )
    assert detector_env.detection_mode == "decisions"


# (1) 未設定 + gpt-4o-mini → llm_score
def test_detection_mode_auto_default_gpt_4o_mini(tmp_articles_file, mock_openai_client, monkeypatch):
    monkeypatch.delenv("DETECTION_MODE", raising=False)
    detector = ViolationDetector(
        openai_client=mock_openai_client,
        judge_model="gpt-4o-mini",
        articles_path=tmp_articles_file,
    )
    assert detector.detection_mode == "llm_score"


# (2) 未設定 + gpt-6-luna → decisions
def test_detection_mode_auto_default_gpt_6_luna(tmp_articles_file, mock_openai_client, monkeypatch, caplog):
    monkeypatch.delenv("DETECTION_MODE", raising=False)
    with caplog.at_level(logging.INFO):
        detector = ViolationDetector(
            openai_client=mock_openai_client,
            judge_model="gpt-6-luna",
            articles_path=tmp_articles_file,
        )
    assert detector.detection_mode == "decisions"
    assert "detection_mode=decisions (auto, judge_model=gpt-6-luna)" in caplog.text


# (3) 未設定 + " GPT-4o-mini-2024-07-18 " → llm_score
def test_detection_mode_auto_with_model_whitespace_and_case(tmp_articles_file, mock_openai_client, monkeypatch):
    monkeypatch.delenv("DETECTION_MODE", raising=False)
    detector = ViolationDetector(
        openai_client=mock_openai_client,
        judge_model=" GPT-4o-mini-2024-07-18 ",
        articles_path=tmp_articles_file,
    )
    assert detector.detection_mode == "llm_score"


# (4) DETECTION_MODE=auto + gpt-6-luna → decisions
def test_detection_mode_env_auto_gpt_6_luna(tmp_articles_file, mock_openai_client, monkeypatch):
    monkeypatch.setenv("DETECTION_MODE", "auto")
    detector = ViolationDetector(
        openai_client=mock_openai_client,
        judge_model="gpt-6-luna",
        articles_path=tmp_articles_file,
    )
    assert detector.detection_mode == "decisions"


# (5) 明示の luna + gpt-4o-mini → luna
def test_detection_mode_explicit_luna_with_gpt_4o_mini(tmp_articles_file, mock_openai_client, monkeypatch):
    monkeypatch.delenv("DETECTION_MODE", raising=False)
    detector = ViolationDetector(
        openai_client=mock_openai_client,
        judge_model="gpt-4o-mini",
        articles_path=tmp_articles_file,
        detection_mode="luna",
    )
    assert detector.detection_mode == "luna"


# (6) 明示の llm_score + gpt-6-luna → llm_score
def test_detection_mode_explicit_llm_score_with_gpt_6_luna(tmp_articles_file, mock_openai_client, monkeypatch):
    monkeypatch.delenv("DETECTION_MODE", raising=False)
    detector = ViolationDetector(
        openai_client=mock_openai_client,
        judge_model="gpt-6-luna",
        articles_path=tmp_articles_file,
        detection_mode="llm_score",
    )
    assert detector.detection_mode == "llm_score"


# (7) 未知の値 "foo" + gpt-6-luna → decisions、+ gpt-4o-mini → llm_score
def test_detection_mode_unknown_value_foo_falls_back_to_auto(tmp_articles_file, mock_openai_client, monkeypatch, caplog):
    monkeypatch.delenv("DETECTION_MODE", raising=False)
    with caplog.at_level(logging.WARNING):
        detector_luna = ViolationDetector(
            openai_client=mock_openai_client,
            judge_model="gpt-6-luna",
            articles_path=tmp_articles_file,
            detection_mode="foo",
        )
    assert detector_luna.detection_mode == "decisions"
    assert "Unknown detection mode 'foo', falling back to 'auto'" in caplog.text

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        detector_mini = ViolationDetector(
            openai_client=mock_openai_client,
            judge_model="gpt-4o-mini",
            articles_path=tmp_articles_file,
            detection_mode="foo",
        )
    assert detector_mini.detection_mode == "llm_score"
    assert "Unknown detection mode 'foo', falling back to 'auto'" in caplog.text


# (8) 引数の指定が環境変数より優先される
def test_detection_mode_argument_precedence_over_env(tmp_articles_file, mock_openai_client, monkeypatch):
    monkeypatch.setenv("DETECTION_MODE", "decisions")
    detector = ViolationDetector(
        openai_client=mock_openai_client,
        judge_model="gpt-6-luna",
        articles_path=tmp_articles_file,
        detection_mode="luna",
    )
    assert detector.detection_mode == "luna"

    monkeypatch.setenv("DETECTION_MODE", "luna")
    detector2 = ViolationDetector(
        openai_client=mock_openai_client,
        judge_model="gpt-4o-mini",
        articles_path=tmp_articles_file,
        detection_mode="llm_score",
    )
    assert detector2.detection_mode == "llm_score"


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


@pytest.fixture
def adversarial_articles_file(tmp_path):
    articles_data = {
        "articles": [
            {
                "id": "course-8-iv",
                "article": "受講規約第8条(iv)",
                "content": "講義資料等の無断転載・複製",
                "category": "知的財産",
            },
            {
                "id": "edu-3-ii",
                "article": "受講生心得第3条(ii)",
                "content": "他者への嫌がらせ行為",
                "category": "迷惑行為",
            },
            {
                "id": "course-10-ip",
                "article": "受講規約第10条(ip)",
                "content": "不正なソフトウェア利用",
                "category": "不正利用",
            },
            {
                "id": "11-xiii",
                "article": "第11条(xiii)",
                "content": "外部コミュニティへの勧誘や宣伝広告",
                "category": "宣伝・勧誘",
            },
            {
                "id": "reg-3",
                "article": "共通規程第3条",
                "content": "アカウントの不正貸与",
                "category": "不正アクセス",
            },
        ]
    }
    file_path = tmp_path / "adversarial_articles.json"
    file_path.write_text(json.dumps(articles_data, ensure_ascii=False), encoding="utf-8")
    return str(file_path)


# 1. _render_template に \d・\1・\g<0>・\n (2 文字) を含む値を入れても例外にならず、値がそのまま入る
def test_render_template_with_escape_sequences():
    template = "Header\n{{body}}\nFooter"
    special_text = r"Pattern \d+ group \1 ref \g<0> newline \n literal"
    result = _render_template(template, body=special_text)
    assert result == f"Header\n{special_text}\nFooter"


# 2. llm_score の経路で、本文に \d を含む投稿でも chat.completions.create が呼ばれ、prompt に本文がそのまま入る
def test_llm_score_mode_with_special_escape_in_post(
    adversarial_articles_file, mock_openai_client, monkeypatch
):
    monkeypatch.setenv("DETECTION_MODE", "llm_score")
    mock_openai_client.chat.completions.create.return_value = MagicMock(
        choices=[
            MagicMock(
                message=MagicMock(
                    content=json.dumps(
                        {"violation_score": 10, "confidence": 0.8, "article_id": None, "reason": "問題なし"}
                    )
                )
            )
        ]
    )

    detector = ViolationDetector(
        openai_client=mock_openai_client,
        judge_model="gpt-4o-mini",
        articles_path=adversarial_articles_file,
    )
    post_text = r"正規表現の \d+ について質問です。"
    result = detector.detect(post_text)

    mock_openai_client.chat.completions.create.assert_called_once()
    call_kwargs = mock_openai_client.chat.completions.create.call_args.kwargs
    prompt = call_kwargs["messages"][0]["content"]
    assert post_text in prompt
    assert result.is_violation is False


# 3. _normalize_article_id: course-8-iv・edu-3-ii・course-10-ip・11-xiii・reg-3 はそのまま。" course-8-iv " (空白付き) も course-8-iv。"course-8-iv 講座受講規約" のような形からも course-8-iv。条文のタイトルを渡すと ID に戻る。None・空文字は None
def test_normalize_article_id_all_patterns(adversarial_articles_file, mock_openai_client):
    detector = ViolationDetector(
        openai_client=mock_openai_client,
        judge_model="gpt-4o-mini",
        articles_path=adversarial_articles_file,
    )

    # course-8-iv・edu-3-ii・course-10-ip・11-xiii・reg-3 はそのまま
    assert detector._normalize_article_id("course-8-iv") == "course-8-iv"
    assert detector._normalize_article_id("edu-3-ii") == "edu-3-ii"
    assert detector._normalize_article_id("course-10-ip") == "course-10-ip"
    assert detector._normalize_article_id("11-xiii") == "11-xiii"
    assert detector._normalize_article_id("reg-3") == "reg-3"

    # " course-8-iv " (空白付き) も course-8-iv
    assert detector._normalize_article_id(" course-8-iv ") == "course-8-iv"

    # "course-8-iv 講座受講規約" のような形からも course-8-iv
    assert detector._normalize_article_id("course-8-iv 講座受講規約") == "course-8-iv"

    # 条文のタイトルを渡すと ID に戻る
    assert detector._normalize_article_id("受講規約第8条(iv)") == "course-8-iv"
    assert detector._normalize_article_id("受講生心得第3条(ii)") == "edu-3-ii"
    assert detector._normalize_article_id("受講規約第10条(ip)") == "course-10-ip"
    assert detector._normalize_article_id("第11条(xiii)") == "11-xiii"
    assert detector._normalize_article_id("共通規程第3条") == "reg-3"

    # None・空文字は None
    assert detector._normalize_article_id(None) is None
    assert detector._normalize_article_id("") is None
    assert detector._normalize_article_id("   ") is None


# 4. decisions の経路で 2 段目が course-8-iv を選んだとき、detect の結果の article_id が course-8-iv で、reason の先頭に [course-8-iv <条文名>] が付く
def test_decisions_mode_stage2_chooses_course_8_iv(
    mocker, adversarial_articles_file, mock_openai_client, monkeypatch
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
                "probabilities": [
                    {"value": "warn", "probability": 0.95},
                    {"value": "safe", "probability": 0.05},
                ],
            },
            {
                "name": "article",
                "type": "choice",
                "probabilities": [
                    {"value": "course-8-iv", "probability": 0.7},
                    {"value": "course-10-ip", "probability": 0.2},
                    {"value": "reg-3", "probability": 0.1},
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
                        {"article_id": "course-8-iv", "reason": "講義資料の転載"}
                    )
                )
            )
        ]
    )

    detector = ViolationDetector(
        openai_client=mock_openai_client,
        judge_model="gpt-6-luna",
        articles_path=adversarial_articles_file,
    )
    result = detector.detect("テスト投稿")

    assert result.is_violation is True
    assert result.confidence == 0.95
    assert result.method == "Decisions"
    assert result.article_id == "course-8-iv"
    assert result.category == "知的財産"
    assert result.reason.startswith("[course-8-iv 受講規約第8条(iv)]")
    assert "講義資料の転載" in result.reason


# 5. _choose_article に空の候補を渡すと API を呼ばずに ValueError
def test_choose_article_empty_candidates_raises_value_error(
    adversarial_articles_file, mock_openai_client
):
    detector = ViolationDetector(
        openai_client=mock_openai_client,
        judge_model="gpt-4o-mini",
        articles_path=adversarial_articles_file,
    )
    mock_openai_client.chat.completions.create.reset_mock()

    with pytest.raises(ValueError):
        detector._choose_article("テスト投稿", [])

    mock_openai_client.chat.completions.create.assert_not_called()

