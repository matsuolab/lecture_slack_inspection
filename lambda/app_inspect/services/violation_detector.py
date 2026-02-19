"""3段階違反検出モジュール: NGワード → RAG → LLM"""
import json
import re
import math
import os
from dataclasses import dataclass
from typing import Optional

_DATA_DIR = os.path.join(os.path.dirname(__file__), "..","..","common","data")

# 追加: Prompt/Schema の置き場所（data配下で統一）
_PROMPTS_DIR = os.path.join(_DATA_DIR, "prompts")
_JUDGE_PROMPT_PATH = os.path.join(_PROMPTS_DIR, "judge_violation.njk")
_RESPONSE_FORMAT_PATH = os.path.join(_PROMPTS_DIR, "judge_violation.response_format.json")

# 追加: Lambdaのグローバルキャッシュ（同一コンテナで再利用）
_PROMPT_TEMPLATE_CACHE: Optional[str] = None
_RESPONSE_FORMAT_CACHE: Optional[dict] = None

_DEFAULT_JUDGE_PROMPT = """あなたはSlack投稿のガイドライン違反を判定するアシスタントです。

## 関連する規約条文
{{articles_text}}

## 投稿内容
{{text}}

## タスク
この投稿が上記の規約条文に違反しているか判定してください。

## 出力形式（JSON）
{"is_violation": true/false, "confidence": 0.0-1.0, "article_id": "該当条文のID", "category": "違反カテゴリ", "reason": "判定理由"}

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
    """judge prompt を初回だけロード（無ければデフォルト）"""
    global _PROMPT_TEMPLATE_CACHE
    if _PROMPT_TEMPLATE_CACHE is not None:
        return _PROMPT_TEMPLATE_CACHE

    if os.path.exists(_JUDGE_PROMPT_PATH):
        _PROMPT_TEMPLATE_CACHE = _read_text(_JUDGE_PROMPT_PATH)
    else:
        _PROMPT_TEMPLATE_CACHE = _DEFAULT_JUDGE_PROMPT
    return _PROMPT_TEMPLATE_CACHE


def _get_response_format() -> dict:
    """response_format を初回だけロード（無ければ json_object）"""
    global _RESPONSE_FORMAT_CACHE
    if _RESPONSE_FORMAT_CACHE is not None:
        return _RESPONSE_FORMAT_CACHE

    if os.path.exists(_RESPONSE_FORMAT_PATH):
        with open(_RESPONSE_FORMAT_PATH, encoding="utf-8") as f:
            _RESPONSE_FORMAT_CACHE = json.load(f)
    else:
        _RESPONSE_FORMAT_CACHE = {"type": "json_object"}
    return _RESPONSE_FORMAT_CACHE


def _render_template(template: str, **vars: str) -> str:
    """{{ var }} を最小実装で置換（依存追加なし）"""
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
    step_stopped: int


class ViolationDetector:
    def __init__(self, openai_client, articles_path: str = None, ng_patterns_path: str = None):
        self.client = openai_client
        self.articles = _load_json_list(
            articles_path or os.path.join(_DATA_DIR, "articles.json"),
            "articles",
        )
        self.ng_patterns = _load_json_list(
            ng_patterns_path or os.path.join(_DATA_DIR, "ng_patterns.json"),
            "patterns",
        )

        # 追加: id<->title を持っておく（LLMがtitleを返しても補正できる）
        self._article_title_by_id = {a["id"]: a.get("article", a["id"]) for a in self.articles}
        self._article_id_by_title = {a.get("article"): a["id"] for a in self.articles if a.get("article")}

        self._embedding_cache = {}

    def detect(self, text: str, course: str = None, skip_llm: bool = False) -> DetectionResult:
        # Step 1: NGワード
        ng_match = self._check_ng_patterns(text, course)
        if ng_match:
            aid = ng_match["article_id"]
            title = self._article_title_by_id.get(aid)
            reason = f"NGパターン検出: {ng_match['pattern'][:50]}"
            if title:
                reason = f"[{aid} {title}] {reason}"

            return DetectionResult(
                is_violation=True,
                confidence=1.0,
                method="NGワード",
                article_id=aid,  # ← IDを返す
                category=ng_match["category"],
                reason=reason,
                step_stopped=1,
            )

        if skip_llm:
            return DetectionResult(
                is_violation=False,
                confidence=0.0,
                method="NGワード",
                article_id=None,
                category=None,
                reason="NGワードに該当なし",
                step_stopped=1,
            )

        # Step 2: RAG（関連条文を検索）
        relevant = self._find_relevant_articles(text, course, top_k=3)

        # Step 3: LLM（条文付きで判定）
        result = self._judge_by_llm(text, relevant)

        aid = self._normalize_article_id(result.get("article_id"))
        title = self._article_title_by_id.get(aid) if aid else None
        reason = result.get("reason", "")
        if aid and title:
            reason = f"[{aid} {title}] {reason}"

        return DetectionResult(
            is_violation=bool(result.get("is_violation", False)),
            confidence=float(result.get("confidence", 0.0)),
            method="LLM",
            article_id=aid,  # ← IDを返す
            category=result.get("category"),
            reason=reason,
            step_stopped=3,
        )

    def _normalize_article_id(self, article_id: Optional[str]) -> Optional[str]:
        if not article_id:
            return None
        s = str(article_id).strip()

        # よくある「A-001 ...」形式からID部分だけ抜く（必要ならパターンを調整）
        m = re.match(r"^([A-Za-z]+-\d+)", s)
        if m:
            s = m.group(1)

        # titleを返してきた場合はIDに戻す
        if s not in self._article_title_by_id and s in self._article_id_by_title:
            s = self._article_id_by_title[s]

        return s

    def _check_ng_patterns(self, text: str, course: str = None) -> Optional[dict]:
        for p in self.ng_patterns:
            courses = p.get("courses", ["ALL"])
            if course and "ALL" not in courses and course not in courses:
                continue
            try:
                if re.search(p["pattern"], text, re.IGNORECASE):
                    return {
                        "pattern": p["pattern"],
                        "article_id": p["article_id"],
                        "category": p["category"],
                    }
            except re.error:
                pass
        return None

    def _find_relevant_articles(self, text: str, course: str = None, top_k: int = 3) -> list:
        articles = self.articles
        if course:
            articles = [
                a for a in articles
                if "ALL" in a.get("courses", ["ALL"]) or course in a.get("courses", [])
            ]

        text_vec = self._get_embedding(text)
        scored = []
        for a in articles:
            content = f"{a['content']} {' '.join(a.get('keywords', []))}"
            aid = a["id"]
            if aid not in self._embedding_cache:
                self._embedding_cache[aid] = self._get_embedding(content)
            sim = self._cosine_sim(text_vec, self._embedding_cache[aid])
            scored.append((a, sim))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [
            {
                "id": a["id"],
                "article": a["article"],
                "category": a["category"],
                "content": a["content"],
                "similarity": round(sim, 3),
            }
            for a, sim in scored[:top_k]
        ]

    def _judge_by_llm(self, text: str, articles: list) -> dict:
        # 重要: IDを含めて渡す（LLMがarticle_idを返せるようにする）
        articles_text = "\n".join(
            [f"- {a['id']} {a.get('article','')}: {a.get('content','')}" for a in articles]
        )

        template = _get_prompt_template()
        prompt = _render_template(template, text=text, articles_text=articles_text)

        try:
            resp = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                response_format=_get_response_format(),
                temperature=0,
            )
            content = (resp.choices[0].message.content or "").strip()
            r = json.loads(content) if content else {}
            return {
                "is_violation": r.get("is_violation", False),
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

    def _get_embedding(self, text: str) -> list:
        resp = self.client.embeddings.create(model="text-embedding-3-small", input=text)
        return resp.data[0].embedding

    def _cosine_sim(self, v1: list, v2: list) -> float:
        dot = sum(a * b for a, b in zip(v1, v2))
        n1 = math.sqrt(sum(a * a for a in v1))
        n2 = math.sqrt(sum(b * b for b in v2))
        return dot / (n1 * n2) if n1 and n2 else 0.0
