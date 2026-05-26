from typing import Optional
from openai import OpenAI
from .models import ModerationResult, normalize_result
from .violation_detector import ViolationDetector


def _confidence_to_severity(confidence: float) -> str:
    """確信度をseverityレベルに変換"""
    if confidence >= 0.8:
        return "high"
    if confidence >= 0.5:
        return "medium"
    return "low"


def run_moderation(
    client: OpenAI,
    model: str,
    message_text: str,
    extra_articles: Optional[list] = None,
) -> ModerationResult:
    """違反検出: 全条文 (articles.json + extra_articles) を LLM に渡して判定。

    extra_articles は WS ローカルルール条文 (Notion 条文マスタ DB の workspace 列で
    紐付いた条文) を想定。
    """
    try:
        detector = ViolationDetector(
            openai_client=client,
            judge_model=model,
        )
        result = detector.detect(message_text, extra_articles=extra_articles)

        return normalize_result({
            "is_violation": result.is_violation,
            "severity": _confidence_to_severity(result.confidence),
            "categories": [result.category] if result.category else [],
            "rationale": result.reason,
            "recommended_reply": "",
            "confidence": result.confidence,
            "article_id": result.article_id,
            "method": result.method,
        })
    except Exception as e:
        print(f"Detection error: {e}")
        return normalize_result({
            "is_violation": False,
            "rationale": f"Error: {e}",
            "method": None,
        })
