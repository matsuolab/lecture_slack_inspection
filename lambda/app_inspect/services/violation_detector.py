"""違反検出モジュール: 全条文 → LLM 判定

旧仕様の RAG (関連条文 top_k=3 抽出) と NG ワード即確定経路は廃止済。
context が十分広いモデルを用いる前提かつ、条文数も少数 (24+α) なので、
articles.json + extra_articles (WS ローカルルール) を全部 LLM に渡す方式に統一。
"""
import json
import os
import re
from dataclasses import dataclass
from typing import Optional

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
_COMMON_DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "common", "data")

_PROMPTS_DIR = os.path.join(_DATA_DIR, "prompts")
_JUDGE_PROMPT_PATH = os.path.join(_PROMPTS_DIR, "judge_violation.txt")
_RESPONSE_FORMAT_PATH = os.path.join(_PROMPTS_DIR, "judge_violation.response_format.json")

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
        embedding_model: str,
        articles_path: str = None,
    ):
        self.client = openai_client
        self.judge_model = judge_model
        self.embedding_model = embedding_model
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

        result = self._judge_by_llm(text, articles)

        aid = self._normalize_article_id(result.get("article_id"))
        title = self._article_title_by_id.get(aid) if aid else None
        reason = result.get("reason", "")
        if aid and title:
            reason = f"[{aid} {title}] {reason}"

        return DetectionResult(
            is_violation=bool(result.get("is_violation", False)),
            confidence=float(result.get("confidence", 0.0)),
            method="LLM",
            article_id=aid,
            category=result.get("category"),
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
