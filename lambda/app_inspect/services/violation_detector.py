"""違反検出モジュール: 全条文 → LLM 判定

旧仕様の RAG (関連条文 top_k=3 抽出) と NG ワード即確定経路は廃止済。
context が十分広いモデルを用いる前提かつ、条文数も少数 (24+α) なので、
articles.json + extra_articles (WS ローカルルール) を全部 LLM に渡す方式に統一。
"""
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Optional

import requests

logger = logging.getLogger(__name__)

DECISIONS_API_URL = "https://api.openai.com/v1/decisions"
DECISIONS_MODEL = "gpt-6-luna"
DECISIONS_TIMEOUT_SECONDS = 10
DECISION_WARN_THRESHOLD = 0.5

TOP_K = 3

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
_COMMON_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "common", "data")

_PROMPTS_DIR = os.path.join(_DATA_DIR, "prompts")
_JUDGE_PROMPT_PATH = os.path.join(_PROMPTS_DIR, "judge_violation.txt")
_RESPONSE_FORMAT_PATH = os.path.join(_PROMPTS_DIR, "judge_violation.response_format.json")
_DECISION_PROMPT_PATH = os.path.join(_PROMPTS_DIR, "judge_decision.txt")
_ARTICLE_GUIDE_PROMPT_PATH = os.path.join(_PROMPTS_DIR, "article_guide.txt")
_JUDGE_ARTICLE_PROMPT_PATH = os.path.join(_PROMPTS_DIR, "judge_article.txt")
_DECISION_CHAT_OUTPUT_PROMPT_PATH = os.path.join(_PROMPTS_DIR, "judge_decision_chat_output.txt")

_DECISION_TEXT_PLACEHOLDER = "(input の投稿)"

_PROMPT_TEMPLATE_CACHE: Optional[str] = None
_RESPONSE_FORMAT_CACHE: Optional[dict] = None
_DECISION_PROMPT_CACHE: Optional[str] = None
_ARTICLE_GUIDE_CACHE: Optional[str] = None
_JUDGE_ARTICLE_CACHE: Optional[str] = None
_DECISION_CHAT_OUTPUT_CACHE: Optional[str] = None

_DEFAULT_JUDGE_PROMPT = """あなたはSlack投稿のガイドライン違反を判定するアシスタントです。

## 関連する規約条文
{{articles_text}}

## 投稿内容
{{text}}

## タスク
この投稿が上記の規約条文に違反しているか判定してください。

## 出力形式（JSON）
{"violation_score": 0から100までの整数, "confidence": 0.0-1.0, "article_id": "該当条文のID", "category": "違反カテゴリ", "reason": "判定理由"}

JSONのみを出力してください。
"""


def _load_json_list(path: str, key: str) -> list:
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f).get(key, [])


def _read_text(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _get_prompt_template() -> str:
    """judge prompt を初回だけロード (無ければデフォルト)"""
    global _PROMPT_TEMPLATE_CACHE
    if _PROMPT_TEMPLATE_CACHE is not None:
        return _PROMPT_TEMPLATE_CACHE

    if os.path.exists(_JUDGE_PROMPT_PATH):
        _PROMPT_TEMPLATE_CACHE = _read_text(_JUDGE_PROMPT_PATH)
    else:
        _PROMPT_TEMPLATE_CACHE = _DEFAULT_JUDGE_PROMPT
    return _PROMPT_TEMPLATE_CACHE


def _get_response_format() -> dict:
    """response_format を初回だけロード (無ければ json_object)"""
    global _RESPONSE_FORMAT_CACHE
    if _RESPONSE_FORMAT_CACHE is not None:
        return _RESPONSE_FORMAT_CACHE

    if os.path.exists(_RESPONSE_FORMAT_PATH):
        with open(_RESPONSE_FORMAT_PATH, encoding="utf-8") as f:
            _RESPONSE_FORMAT_CACHE = json.load(f)
    else:
        _RESPONSE_FORMAT_CACHE = {"type": "json_object"}
    return _RESPONSE_FORMAT_CACHE


def _get_decision_prompt_template() -> str:
    """judge_decision prompt を初回だけロード (無ければ空文字)"""
    global _DECISION_PROMPT_CACHE
    if _DECISION_PROMPT_CACHE is not None:
        return _DECISION_PROMPT_CACHE

    if os.path.exists(_DECISION_PROMPT_PATH):
        _DECISION_PROMPT_CACHE = _read_text(_DECISION_PROMPT_PATH)
    else:
        _DECISION_PROMPT_CACHE = ""
    return _DECISION_PROMPT_CACHE


def _get_article_guide_template() -> str:
    """article_guide を初回だけロード (無ければ空文字)"""
    global _ARTICLE_GUIDE_CACHE
    if _ARTICLE_GUIDE_CACHE is not None:
        return _ARTICLE_GUIDE_CACHE

    if os.path.exists(_ARTICLE_GUIDE_PROMPT_PATH):
        _ARTICLE_GUIDE_CACHE = _read_text(_ARTICLE_GUIDE_PROMPT_PATH)
    else:
        _ARTICLE_GUIDE_CACHE = ""
    return _ARTICLE_GUIDE_CACHE


def _get_judge_article_template() -> str:
    """judge_article prompt を初回だけロード (無ければ空文字)"""
    global _JUDGE_ARTICLE_CACHE
    if _JUDGE_ARTICLE_CACHE is not None:
        return _JUDGE_ARTICLE_CACHE

    if os.path.exists(_JUDGE_ARTICLE_PROMPT_PATH):
        _JUDGE_ARTICLE_CACHE = _read_text(_JUDGE_ARTICLE_PROMPT_PATH)
    else:
        _JUDGE_ARTICLE_CACHE = ""
    return _JUDGE_ARTICLE_CACHE


def _get_decision_chat_output_template() -> str:
    """judge_decision_chat_output prompt を初回だけロード (無ければ空文字)"""
    global _DECISION_CHAT_OUTPUT_CACHE
    if _DECISION_CHAT_OUTPUT_CACHE is not None:
        return _DECISION_CHAT_OUTPUT_CACHE

    if os.path.exists(_DECISION_CHAT_OUTPUT_PROMPT_PATH):
        _DECISION_CHAT_OUTPUT_CACHE = _read_text(_DECISION_CHAT_OUTPUT_PROMPT_PATH)
    else:
        _DECISION_CHAT_OUTPUT_CACHE = ""
    return _DECISION_CHAT_OUTPUT_CACHE


def _render_template(template: str, **vars: str) -> str:
    """{{ var }} を最小実装で置換 (依存追加なし)"""
    out = template
    for k, v in vars.items():
        out = re.sub(r"{{\s*" + re.escape(k) + r"\s*}}", str(v), out)
    return out


@dataclass
class DetectionResult:
    is_violation: bool
    confidence: float
    method: str
    article_id: Optional[str]
    category: Optional[str]
    reason: str


class ViolationDetector:
    def __init__(
        self,
        openai_client,
        judge_model: str,
        articles_path: Optional[str] = None,
        detection_mode: Optional[str] = None,
    ):
        self.client = openai_client
        self.judge_model = judge_model

        mode = detection_mode if detection_mode is not None else os.environ.get("DETECTION_MODE")
        if not mode:
            mode = "llm_score"
        if mode not in ("llm_score", "decisions", "luna"):
            logger.warning("Unknown detection mode '%s', falling back to 'llm_score'", mode)
            mode = "llm_score"
        self.detection_mode = mode

        self.articles = _load_json_list(
            articles_path or os.path.join(_COMMON_DATA_DIR, "articles.json"),
            "articles",
        )
        self._article_title_by_id = {a["id"]: a.get("article", a["id"]) for a in self.articles}
        self._article_id_by_title = {
            a.get("article"): a["id"] for a in self.articles if a.get("article")
        }

    def detect(self, text: str, extra_articles: Optional[list] = None) -> DetectionResult:
        """articles.json + extra_articles を全部 LLM に渡して判定。

        extra_articles は WS ローカルルール条文。
        各要素は {id, article, content, [category, regulation]} を持つ想定。
        """
        articles = list(self.articles)
        if extra_articles:
            articles.extend(extra_articles)
            # 追加条文の id ↔ article マッピングを動的に拡張 (表示用解決のため)
            for a in extra_articles:
                aid = a.get("id")
                article = a.get("article", aid)
                if aid:
                    self._article_title_by_id[aid] = article
                if article:
                    self._article_id_by_title[article] = aid

        if self.detection_mode == "decisions":
            try:
                result = self._judge_by_decisions(text, articles)
                method = "Decisions"
            except Exception as e:
                logger.warning("Decisions API failed, falling back to chat completions: %s", e)
                result = self._judge_by_chat_pipeline(text, articles)
                method = "LLM-fallback"
        elif self.detection_mode == "luna":
            result = self._judge_by_chat_pipeline(text, articles)
            method = "Luna"
        else:
            result = self._judge_by_llm(text, articles)
            method = "LLM"

        aid = self._normalize_article_id(result.get("article_id"))
        title = self._article_title_by_id.get(aid) if aid else None
        reason = result.get("reason", "")
        if aid and title:
            reason = f"[{aid} {title}] {reason}"

        category = result.get("category")
        if aid:
            for a in articles:
                if a.get("id") == aid:
                    category = a.get("category")
                    break

        return DetectionResult(
            is_violation=bool(result.get("is_violation", False)),
            confidence=float(result.get("confidence", 0.0)),
            method=method,
            article_id=aid,
            category=category,
            reason=reason,
        )

    def _normalize_article_id(self, article_id: Optional[str]) -> Optional[str]:
        if not article_id:
            return None
        s = str(article_id).strip()

        # よくある「A-001 ...」形式から ID 部分だけ抜く
        m = re.match(r"^([A-Za-z]+-\d+)", s)
        if m:
            s = m.group(1)

        # title を返してきた場合は ID に戻す
        if s not in self._article_title_by_id and s in self._article_id_by_title:
            s = self._article_id_by_title[s]

        return s

    def _judge_by_llm(self, text: str, articles: list) -> dict:
        articles_text = "\n".join(
            [f"- {a['id']} {a.get('article','')}: {a.get('content','')}" for a in articles]
        )

        template = _get_prompt_template()
        prompt = _render_template(template, text=text, articles_text=articles_text)

        try:
            resp = self.client.chat.completions.create(
                model=self.judge_model,
                messages=[{"role": "user", "content": prompt}],
                response_format=_get_response_format(),
                temperature=0,
            )
            content = (resp.choices[0].message.content or "").strip()
            r = json.loads(content) if content else {}

            score = r.get("violation_score", 0)

            return {
                "is_violation": score >= 51,
                "confidence": r.get("confidence", 0.5),
                "article_id": r.get("article_id"),
                "category": r.get("category"),
                "reason": r.get("reason", ""),
            }
        except Exception as e:
            return {
                "is_violation": False,
                "confidence": 0.0,
                "article_id": None,
                "category": None,
                "reason": f"LLM判定エラー: {e}",
            }

    def _choose_article(self, text: str, candidates: list[dict]) -> dict:
        candidates_text = "\n".join(
            [
                f"- {c.get('id', '')} {c.get('article') or ''} [{c.get('category') or ''}]: {c.get('content') or ''}"
                for c in candidates
            ]
        )
        guide = _get_article_guide_template()
        template = _get_judge_article_template()
        prompt_template = _render_template(
            template, article_guide=guide, candidates_text=candidates_text
        )
        prompt = _render_template(prompt_template, text=text)

        candidate_ids = [c["id"] for c in candidates if "id" in c]
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "article_choice",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "article_id": {"type": "string", "enum": candidate_ids},
                        "reason": {"type": "string"},
                    },
                    "required": ["article_id", "reason"],
                    "additionalProperties": False,
                },
            },
        }

        resp = self.client.chat.completions.create(
            model=self.judge_model,
            messages=[{"role": "user", "content": prompt}],
            response_format=response_format,
        )
        content = (resp.choices[0].message.content or "").strip()
        r = json.loads(content) if content else {}
        return {
            "article_id": r.get("article_id"),
            "reason": r.get("reason", ""),
        }

    def _judge_by_decisions(self, text: str, articles: list) -> dict:
        articles_text = "\n".join(
            [f"- {a['id']} {a.get('article', '')}: {a.get('content', '')}" for a in articles]
        )

        template = _get_decision_prompt_template()
        rendered_template = _render_template(template, articles_text=articles_text)

        guide = _get_article_guide_template()
        article_instructions = (
            "この投稿が警告の対象だとしたら、最も当てはまる条文を 1 つ選ぶ。\n\n"
            + guide
            + "\n## 条文\n"
            + articles_text
        )

        payload = {
            "model": DECISIONS_MODEL,
            "input": f"## 投稿内容\n```\n{text}\n```",
            "questions": [
                {
                    "type": "choice",
                    "name": "verdict",
                    "instructions": f"{rendered_template}\n運営がこの投稿者に警告を送るべきなら warn、そうでなければ safe を選ぶ。",
                    "choices": [{"value": "warn"}, {"value": "safe"}],
                },
                {
                    "type": "choice",
                    "name": "article",
                    "instructions": article_instructions,
                    "choices": [{"value": a["id"]} for a in articles],
                },
            ],
        }

        resp = requests.post(
            DECISIONS_API_URL,
            headers={
                "Authorization": f"Bearer {self.client.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=DECISIONS_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()

        data = resp.json()
        answers = data.get("answers", [])

        verdict_ans = next((ans for ans in answers if ans.get("name") == "verdict"), None)
        if not verdict_ans:
            raise ValueError("verdict answer is missing in Decisions API response")
        if verdict_ans.get("type") != "choice":
            raise ValueError(f"verdict answer type is not 'choice': {verdict_ans.get('type')}")

        p_warn = None
        for p in verdict_ans.get("probabilities", []):
            if p.get("value") == "warn":
                p_warn = p.get("probability")
                break

        if p_warn is None:
            raise ValueError("warn probability is missing in verdict answer")

        p_warn = float(p_warn)

        article_ans = next((ans for ans in answers if ans.get("name") == "article"), None)
        if not article_ans:
            raise ValueError("article answer is missing in Decisions API response")
        if article_ans.get("type") != "choice":
            raise ValueError(f"article answer type is not 'choice': {article_ans.get('type')}")

        article_probs = article_ans.get("probabilities", [])
        sorted_probs = sorted(
            article_probs,
            key=lambda x: x.get("probability", 0.0),
            reverse=True,
        )
        candidate_ids = [p["value"] for p in sorted_probs if "value" in p][:TOP_K]

        article_by_id = {a["id"]: a for a in articles}
        candidate_dicts = [article_by_id[cid] for cid in candidate_ids if cid in article_by_id]
        candidate_ids = [c["id"] for c in candidate_dicts]

        is_warn = (p_warn >= DECISION_WARN_THRESHOLD)
        article_id = None
        reason = f"Decisions API: P(warn)={p_warn:.2f}"

        if is_warn:
            try:
                chosen = self._choose_article(text, candidate_dicts)
                article_id = chosen.get("article_id")
                reason = chosen.get("reason", "")
            except Exception as e:
                logger.warning("Article choice failed in decisions mode: %s", e)
                article_id = candidate_ids[0] if candidate_ids else None
                reason = f"Decisions API: P(warn)={p_warn:.2f}"
        else:
            article_id = None

        category = None
        if article_id:
            for a in articles:
                if a.get("id") == article_id:
                    category = a.get("category")
                    break

        return {
            "is_violation": is_warn,
            "confidence": p_warn,
            "article_id": article_id,
            "category": category,
            "reason": reason,
            "candidates": candidate_ids,
        }

    def _judge_by_chat_pipeline(self, text: str, articles: list) -> dict:
        articles_text = "\n".join(
            [f"- {a['id']} {a.get('article', '')}: {a.get('content', '')}" for a in articles]
        )

        template = _get_decision_prompt_template()
        part1 = _render_template(template, articles_text=articles_text).replace(
            _DECISION_TEXT_PLACEHOLDER, text
        )
        part2 = _get_article_guide_template()
        part3 = _get_decision_chat_output_template()
        prompt = part1 + part2 + part3

        article_ids = [a["id"] for a in articles]
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "warn_decision_cands",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "decision": {"type": "string", "enum": ["warn", "safe"]},
                        "candidates": {
                            "type": "array",
                            "items": {"type": "string", "enum": article_ids},
                        },
                        "reason": {"type": "string"},
                    },
                    "required": ["decision", "candidates", "reason"],
                    "additionalProperties": False,
                },
            },
        }

        try:
            resp = self.client.chat.completions.create(
                model=self.judge_model,
                messages=[{"role": "user", "content": prompt}],
                response_format=response_format,
            )
            content = (resp.choices[0].message.content or "").strip()
            r = json.loads(content) if content else {}

            decision = r.get("decision")
            is_warn = (decision == "warn")
            step1_reason = r.get("reason", "")

            valid_ids = {a["id"] for a in articles}
            raw_candidates = r.get("candidates", [])
            if not isinstance(raw_candidates, list):
                raw_candidates = []

            candidate_ids = []
            for cid in raw_candidates:
                if cid in valid_ids and cid not in candidate_ids:
                    candidate_ids.append(cid)
                    if len(candidate_ids) == TOP_K:
                        break

            if not candidate_ids:
                if "11-xvi" in valid_ids:
                    candidate_ids = ["11-xvi"]
                elif articles:
                    candidate_ids = [articles[0]["id"]]
                else:
                    candidate_ids = []

            article_by_id = {a["id"]: a for a in articles}
            candidate_dicts = [article_by_id[cid] for cid in candidate_ids if cid in article_by_id]

            if is_warn:
                try:
                    chosen = self._choose_article(text, candidate_dicts)
                    article_id = chosen.get("article_id")
                    step2_reason = chosen.get("reason", "")
                except Exception as e:
                    logger.warning("Article choice failed in chat pipeline: %s", e)
                    article_id = candidate_ids[0] if candidate_ids else None
                    step2_reason = step1_reason
                final_reason = f"[luna] {step2_reason}"
            else:
                article_id = None
                final_reason = f"[luna] {step1_reason}"

            category = None
            if article_id:
                for a in articles:
                    if a.get("id") == article_id:
                        category = a.get("category")
                        break

            return {
                "is_violation": is_warn,
                "confidence": 0.8 if is_warn else 0.2,
                "article_id": article_id,
                "category": category,
                "reason": final_reason,
                "candidates": candidate_ids,
            }
        except Exception as e:
            return {
                "is_violation": False,
                "confidence": 0.0,
                "article_id": None,
                "category": None,
                "reason": f"LLM判定エラー: {e}",
            }
