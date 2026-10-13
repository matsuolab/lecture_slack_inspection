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
    _render_template,
)


@pytest.fixture
def tmp_articles_file(tmp_path):
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
            {
                "id": "reg-3",
                "article": "共通規程第3条",
                "content": "アカウントの不正貸与",
                "category": "不正アクセス",
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


def _make_decisions_response(p_warn: float, article_probs: list[tuple[str, float]]) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "answers": [
            {
                "name": "verdict",
                "type": "choice",
                "probabilities": [
                    {"value": "warn", "probability": p_warn},
                    {"value": "safe", "probability": round(1.0 - p_warn, 4)},
                ],
            },
            {
                "name": "article",
                "type": "choice",
                "probabilities": [
                    {"value": aid, "probability": prob} for aid, prob in article_probs
                ],
            },
        ]
    }
    return mock_resp


def _make_chat_response(content: dict | str) -> MagicMock:
    if isinstance(content, dict):
        content_str = json.dumps(content, ensure_ascii=False)
    else:
        content_str = content
    return MagicMock(
        choices=[
            MagicMock(
                message=MagicMock(content=content_str)
            )
        ]
    )


# モード決定テスト (既定・auto・大文字や空白・明示・未知の値・引数と環境変数の優先を1つに集約)
@pytest.mark.parametrize(
    "env_mode,arg_mode,judge_model,expected_mode",
    [
        # 既定 (未設定)
        (None, None, "gpt-4o-mini", "llm_score"),
        (None, None, "gpt-6-luna", "decisions"),
        (None, None, " GPT-4o-mini-2024-07-18 ", "llm_score"),
        # auto
        ("auto", None, "gpt-6-luna", "decisions"),
        ("auto", None, "gpt-4o-mini", "llm_score"),
        # 大文字や空白
        (" DECISIONS ", None, "gpt-6-luna", "decisions"),
        (None, " LUNA ", "gpt-4o-mini", "luna"),
        # 明示
        (None, "decisions", "gpt-6-luna", "decisions"),
        (None, "luna", "gpt-4o-mini", "luna"),
        (None, "llm_score", "gpt-6-luna", "llm_score"),
        ("decisions", None, "gpt-4o-mini", "decisions"),
        # 未知の値 (autoへフォールバック)
        (None, "unknown_mode", "gpt-6-luna", "decisions"),
        ("invalid_env_mode", None, "gpt-6-luna", "decisions"),
        (None, "foo", "gpt-6-luna", "decisions"),
        (None, "foo", "gpt-4o-mini", "llm_score"),
        # 引数と環境変数の優先 (引数優先)
        ("decisions", "luna", "gpt-6-luna", "luna"),
        ("luna", "llm_score", "gpt-4o-mini", "llm_score"),
    ],
)
def test_detection_mode_resolution(
    tmp_articles_file, mock_openai_client, monkeypatch, env_mode, arg_mode, judge_model, expected_mode
):
    if env_mode is not None:
        monkeypatch.setenv("DETECTION_MODE", env_mode)
    else:
        monkeypatch.delenv("DETECTION_MODE", raising=False)

    detector = ViolationDetector(
        openai_client=mock_openai_client,
        judge_model=judge_model,
        articles_path=tmp_articles_file,
        detection_mode=arg_mode,
    )
    assert detector.detection_mode == expected_mode


# llm_score 経路: temperature を渡さないことの確認
def test_llm_score_mode_does_not_pass_temperature(
    mocker, tmp_articles_file, mock_openai_client, monkeypatch
):
    monkeypatch.setenv("DETECTION_MODE", "llm_score")
    mock_post = mocker.patch("app_inspect.services.violation_detector.requests.post")
    mock_openai_client.chat.completions.create.return_value = _make_chat_response(
        {"violation_score": 60, "confidence": 0.8, "article_id": "11-v", "reason": "違反"}
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
    assert "temperature" not in call_kwargs
    assert result.is_violation is True
    assert result.method == "LLM"
    assert result.article_id == "11-v"
    assert result.category == "勧誘行為"
    assert result.reason.startswith("[11-v 第11条(v)]")


# llm_score 経路: 例外のときは送り直さず LLM判定エラー
def test_llm_score_mode_exception_does_not_retry_and_returns_error(
    mocker, tmp_articles_file, mock_openai_client, monkeypatch
):
    monkeypatch.setenv("DETECTION_MODE", "llm_score")
    mock_post = mocker.patch("app_inspect.services.violation_detector.requests.post")
    mock_openai_client.chat.completions.create.side_effect = RuntimeError("API failure")

    detector = ViolationDetector(
        openai_client=mock_openai_client,
        judge_model="gpt-4o-mini",
        articles_path=tmp_articles_file,
    )
    result = detector.detect("テスト投稿")

    mock_post.assert_not_called()
    mock_openai_client.chat.completions.create.assert_called_once()
    assert result.is_violation is False
    assert result.confidence == 0.0
    assert result.article_id is None
    assert result.category is None
    assert result.method == "LLM"
    assert result.reason.startswith("LLM判定エラー: API failure")


# decisions: warn p=0.9、article の確率が 11-xiii 0.6 / 11-v 0.3 / 11-xvi 0.1
def test_decisions_mode_warn_stage2_choice(mocker, tmp_articles_file, mock_openai_client, monkeypatch):
    monkeypatch.setenv("DETECTION_MODE", "decisions")
    mock_post = mocker.patch("app_inspect.services.violation_detector.requests.post")
    mock_post.return_value = _make_decisions_response(
        0.9, [("11-xiii", 0.6), ("11-v", 0.3), ("11-xvi", 0.1)]
    )

    mock_openai_client.chat.completions.create.return_value = _make_chat_response(
        {"article_id": "11-v", "reason": "宗教勧誘に該当"}
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


# decisions: article の choices に none が無い。verdict の instructions の組み立て
def test_decisions_request_payload_choices_and_instructions(
    mocker, tmp_articles_file, mock_openai_client, monkeypatch
):
    monkeypatch.setenv("DETECTION_MODE", "decisions")
    mock_post = mocker.patch("app_inspect.services.violation_detector.requests.post")
    mock_post.return_value = _make_decisions_response(
        0.2, [("11-v", 0.5), ("11-xiii", 0.3), ("11-xvi", 0.2)]
    )

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
    assert choice_values == [
        "course-8-iv",
        "edu-3-ii",
        "course-10-ip",
        "11-v",
        "11-xiii",
        "11-xvi",
        "reg-3",
    ]


# decisions: safe (p=0.2) → 2 段目を呼ばない、article_id None
def test_decisions_mode_safe_no_stage2(mocker, tmp_articles_file, mock_openai_client, monkeypatch):
    monkeypatch.setenv("DETECTION_MODE", "decisions")
    mock_post = mocker.patch("app_inspect.services.violation_detector.requests.post")
    mock_post.return_value = _make_decisions_response(
        0.2, [("11-v", 0.4), ("11-xiii", 0.3), ("11-xvi", 0.3)]
    )

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


# decisions: 2 段目が例外 → 候補の 1 位が article_id、reason に P(warn)=0.90
def test_decisions_mode_stage2_exception_fallback(mocker, tmp_articles_file, mock_openai_client, monkeypatch):
    monkeypatch.setenv("DETECTION_MODE", "decisions")
    mock_post = mocker.patch("app_inspect.services.violation_detector.requests.post")
    mock_post.return_value = _make_decisions_response(
        0.9, [("11-xiii", 0.6), ("11-v", 0.3), ("11-xvi", 0.1)]
    )

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


# decisions: Decisions API が例外 → 経路 B に切り替わる (method "LLM-fallback")
def test_decisions_mode_api_exception_triggers_fallback(
    mocker, tmp_articles_file, mock_openai_client, monkeypatch
):
    monkeypatch.setenv("DETECTION_MODE", "decisions")
    mocker.patch(
        "app_inspect.services.violation_detector.requests.post",
        side_effect=requests.HTTPError("500 Server Error"),
    )

    mock_openai_client.chat.completions.create.side_effect = [
        _make_chat_response(
            {"decision": "warn", "candidates": ["11-v", "11-xiii", "11-xvi"], "reason": "外部勧誘の疑い"}
        ),
        _make_chat_response(
            {"article_id": "11-xiii", "reason": "外部グループ告知"}
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


# luna: requests.post を呼ばない。1 段目の prompt に投稿本文と article_guide の文面と出力指定が入り、
# (input の投稿) が残っていない。warn → 2 段目 → method "Luna"、reason が [luna] を含む
def test_luna_mode_warn_pipeline(mocker, tmp_articles_file, mock_openai_client, monkeypatch):
    monkeypatch.setenv("DETECTION_MODE", "luna")
    mock_post = mocker.patch("app_inspect.services.violation_detector.requests.post")

    mock_openai_client.chat.completions.create.side_effect = [
        _make_chat_response(
            {"decision": "warn", "candidates": ["11-v", "11-xiii"], "reason": "勧誘疑い"}
        ),
        _make_chat_response(
            {"article_id": "11-v", "reason": "マルチ勧誘行為"}
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


# luna: 候補に条文一覧に無い ID や重複がある → 除かれる。全部無効なら 11-xvi
def test_luna_mode_candidate_sanitization(mocker, tmp_articles_file, mock_openai_client, monkeypatch):
    monkeypatch.setenv("DETECTION_MODE", "luna")
    mock_post = mocker.patch("app_inspect.services.violation_detector.requests.post")

    mock_openai_client.chat.completions.create.side_effect = [
        _make_chat_response(
            {"decision": "warn", "candidates": ["unknown-1", "11-v", "11-v", "11-xiii"], "reason": "テスト"}
        ),
        _make_chat_response(
            {"article_id": "11-v", "reason": "理由"}
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
        _make_chat_response(
            {"decision": "warn", "candidates": ["invalid-1", "invalid-2"], "reason": "テスト2"}
        ),
        _make_chat_response(
            {"article_id": "11-xvi", "reason": "不適切行為"}
        ),
    ]

    result2 = detector.detect("テスト投稿2")
    call2_kwargs2 = mock_openai_client.chat.completions.create.call_args_list[1].kwargs
    schema2 = call2_kwargs2["response_format"]["json_schema"]["schema"]
    assert schema2["properties"]["article_id"]["enum"] == ["11-xvi"]
    assert result2.article_id == "11-xvi"


# luna: 1 段目が例外 → is_violation False、reason が "LLM判定エラー" で始まる
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


# 2 段目の prompt で、投稿本文に {{article_guide}} という文字列があっても展開されない
def test_stage2_prompt_preserves_template_variable_in_text(
    mocker, tmp_articles_file, mock_openai_client, monkeypatch
):
    monkeypatch.setenv("DETECTION_MODE", "luna")
    mock_openai_client.chat.completions.create.side_effect = [
        _make_chat_response(
            {"decision": "warn", "candidates": ["11-v"], "reason": "疑い"}
        ),
        _make_chat_response(
            {"article_id": "11-v", "reason": "違反理由"}
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


def test_decisions_mode_boundary_threshold(mocker, tmp_articles_file, mock_openai_client, monkeypatch):
    monkeypatch.setenv("DETECTION_MODE", "decisions")
    mock_post = mocker.patch("app_inspect.services.violation_detector.requests.post")
    mock_post.return_value = _make_decisions_response(
        0.5, [("11-v", 0.5), ("11-xiii", 0.3), ("11-xvi", 0.2)]
    )

    mock_openai_client.chat.completions.create.return_value = _make_chat_response(
        {"article_id": "11-v", "reason": "境界値"}
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
    mock_openai_client.chat.completions.create.return_value = _make_chat_response(
        {"decision": "safe", "candidates": ["11-v", "11-xiii", "11-xvi"], "reason": "安全な挨拶"}
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
        _make_chat_response(
            {"decision": "warn", "candidates": ["11-xiii", "11-v"], "reason": "外部宣伝の疑い"}
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


# _render_template に \d・\1・\g<0>・\n (2 文字) を含む値を入れても例外にならず、値がそのまま入る
def test_render_template_with_escape_sequences():
    template = "Header\n{{body}}\nFooter"
    special_text = r"Pattern \d+ group \1 ref \g<0> newline \n literal"
    result = _render_template(template, body=special_text)
    assert result == f"Header\n{special_text}\nFooter"


# llm_score の経路で、本文に \d を含む投稿でも chat.completions.create が呼ばれ、prompt に本文がそのまま入る
def test_llm_score_mode_with_special_escape_in_post(
    tmp_articles_file, mock_openai_client, monkeypatch
):
    monkeypatch.setenv("DETECTION_MODE", "llm_score")
    mock_openai_client.chat.completions.create.return_value = _make_chat_response(
        {"violation_score": 10, "confidence": 0.8, "article_id": None, "reason": "問題なし"}
    )

    detector = ViolationDetector(
        openai_client=mock_openai_client,
        judge_model="gpt-4o-mini",
        articles_path=tmp_articles_file,
    )
    post_text = r"正規表現の \d+ について質問です。"
    result = detector.detect(post_text)

    mock_openai_client.chat.completions.create.assert_called_once()
    call_kwargs = mock_openai_client.chat.completions.create.call_args.kwargs
    prompt = call_kwargs["messages"][0]["content"]
    assert post_text in prompt
    assert result.is_violation is False


# _normalize_article_id: course-8-iv・edu-3-ii・course-10-ip・11-xiii・reg-3 はそのまま。" course-8-iv " (空白付き) も course-8-iv。"course-8-iv 講座受講規約" のような形からも course-8-iv。条文のタイトルを渡すと ID に戻る。None・空文字は None
def test_normalize_article_id_all_patterns(tmp_articles_file, mock_openai_client):
    detector = ViolationDetector(
        openai_client=mock_openai_client,
        judge_model="gpt-4o-mini",
        articles_path=tmp_articles_file,
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


# decisions の経路で 2 段目が course-8-iv を選んだとき、detect の結果の article_id が course-8-iv で、reason の先頭に [course-8-iv <条文名>] が付く
def test_decisions_mode_stage2_chooses_course_8_iv(
    mocker, tmp_articles_file, mock_openai_client, monkeypatch
):
    monkeypatch.setenv("DETECTION_MODE", "decisions")
    mock_post = mocker.patch("app_inspect.services.violation_detector.requests.post")
    mock_post.return_value = _make_decisions_response(
        0.95, [("course-8-iv", 0.7), ("course-10-ip", 0.2), ("reg-3", 0.1)]
    )

    mock_openai_client.chat.completions.create.return_value = _make_chat_response(
        {"article_id": "course-8-iv", "reason": "講義資料の転載"}
    )

    detector = ViolationDetector(
        openai_client=mock_openai_client,
        judge_model="gpt-6-luna",
        articles_path=tmp_articles_file,
    )
    result = detector.detect("テスト投稿")

    assert result.is_violation is True
    assert result.confidence == 0.95
    assert result.method == "Decisions"
    assert result.article_id == "course-8-iv"
    assert result.category == "知的財産"
    assert result.reason.startswith("[course-8-iv 受講規約第8条(iv)]")
    assert "講義資料の転載" in result.reason


# _choose_article に空の候補を渡すと API を呼ばずに ValueError
def test_choose_article_empty_candidates_raises_value_error(
    tmp_articles_file, mock_openai_client
):
    detector = ViolationDetector(
        openai_client=mock_openai_client,
        judge_model="gpt-4o-mini",
        articles_path=tmp_articles_file,
    )
    mock_openai_client.chat.completions.create.reset_mock()

    with pytest.raises(ValueError):
        detector._choose_article("テスト投稿", [])

    mock_openai_client.chat.completions.create.assert_not_called()
