"""違反検出モジュール"""
import json
import re
import math
from dataclasses import dataclass
from typing import Optional

from openai import OpenAI


@dataclass
class DetectionResult:
    is_violation: bool
    confidence: float
    method: str
    article_id: Optional[str]
    category: Optional[str]
    reason: str
    step_stopped: int


def _load_json_list(path: str, key: str) -> list:
    """JSONファイルからリストを読み込む"""
    if not path:
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f).get(key, [])


class ViolationDetector:

    def __init__(self, openai_api_key: str, articles_path: str = None, ng_patterns_path: str = None):
        self.client = OpenAI(api_key=openai_api_key)
        self.articles = _load_json_list(articles_path, "articles")
        self.ng_patterns = _load_json_list(ng_patterns_path, "patterns")
        self._article_name_map = {a["id"]: a.get("article", a["id"]) for a in self.articles}
        self._embedding_cache = {}

    def detect(self, text: str, course: str = None, skip_llm: bool = False) -> DetectionResult:
        # Step 1: NGワード
        ng_match = self._check_ng_patterns(text, course)
        if ng_match:
            return DetectionResult(
                is_violation=True,
                confidence=1.0,
                method="NGワード",
                article_id=self._get_article_name(ng_match["article_id"]),
                category=ng_match["category"],
                reason=f"NGパターン検出: {ng_match['pattern'][:50]}",
                step_stopped=1,
            )

        if skip_llm:
            return DetectionResult(
                is_violation=False, confidence=0.0, method="NGワード",
                article_id=None, category=None,
                reason="NGワードに該当なし", step_stopped=1,
            )

        # Step 2: RAG
        relevant = self._find_relevant_articles(text, course, top_k=3)

        # Step 3: LLM
        result = self._judge_by_llm(text, relevant)
        article_name = self._get_article_name(result.get("article_id"))

        return DetectionResult(
            is_violation=result["is_violation"],
            confidence=result["confidence"],
            method="LLM",
            article_id=article_name,
            category=result.get("category"),
            reason=result["reason"],
            step_stopped=3,
        )

    def _get_article_name(self, article_id: str) -> Optional[str]:
        if not article_id:
            return None
        return self._article_name_map.get(article_id, article_id)

    def _check_ng_patterns(self, text: str, course: str = None) -> Optional[dict]:
        for p in self.ng_patterns:
            courses = p.get("courses", ["ALL"])
            if course and "ALL" not in courses and course not in courses:
                continue
            try:
                if re.search(p["pattern"], text, re.IGNORECASE):
                    return {"pattern": p["pattern"], "article_id": p["article_id"], "category": p["category"]}
            except re.error:
                pass
        return None

    def _find_relevant_articles(self, text: str, course: str = None, top_k: int = 3) -> list:
        articles = self.articles
        if course:
            articles = [a for a in articles
                        if "ALL" in a.get("courses", ["ALL"]) or course in a.get("courses", [])]

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
            {"id": a["id"], "article": a["article"], "category": a["category"],
             "content": a["content"], "similarity": round(sim, 3)}
            for a, sim in scored[:top_k]
        ]

    def _judge_by_llm(self, text: str, articles: list) -> dict:
        articles_text = "\n".join([f"- {a['article']}: {a['content']}" for a in articles])
        prompt = f"""あなたはSlack投稿のガイドライン違反を判定するアシスタントです。

## 関連する規約条文
{articles_text}

## 投稿内容
{text}

## タスク
この投稿が上記の規約条文に違反しているか判定してください。

## 出力形式（JSON）
{{"is_violation": true/false, "confidence": 0.0-1.0, "article_id": "該当条文のID", "category": "違反カテゴリ", "reason": "判定理由"}}

JSONのみを出力してください。"""

        try:
            resp = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
            )
            result_text = resp.choices[0].message.content.strip()

            # マークダウンのコードブロックを除去
            if "```json" in result_text:
                result_text = result_text.split("```json")[1].split("```")[0]
            elif "```" in result_text:
                result_text = result_text.split("```")[1].split("```")[0]

            r = json.loads(result_text)
            return {
                "is_violation": r.get("is_violation", False),
                "confidence": r.get("confidence", 0.5),
                "article_id": r.get("article_id"),
                "category": r.get("category"),
                "reason": r.get("reason", ""),
            }
        except Exception as e:
            return {"is_violation": False, "confidence": 0.0, "article_id": None,
                    "category": None, "reason": f"LLM判定エラー: {e}"}

    def _get_embedding(self, text: str) -> list:
        resp = self.client.embeddings.create(model="text-embedding-3-small", input=text)
        return resp.data[0].embedding

    def _cosine_sim(self, v1: list, v2: list) -> float:
        dot = sum(a * b for a, b in zip(v1, v2))
        n1 = math.sqrt(sum(a * a for a in v1))
        n2 = math.sqrt(sum(b * b for b in v2))
        return dot / (n1 * n2) if n1 and n2 else 0.0
