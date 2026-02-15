import json
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


def run_moderation(client: OpenAI, model: str, guidelines: str, message_text: str) -> ModerationResult:
    """3段階違反検出: NGワード → RAG → LLM"""
    try:
        detector = ViolationDetector(openai_client=client)
        result = detector.detect(message_text)

        return normalize_result({
            "is_violation": result.is_violation,
            "severity": _confidence_to_severity(result.confidence),
            "categories": [result.category] if result.category else [],
            "rationale": result.reason,
            "recommended_reply": "",
        })
    except Exception as e:
        print(f"Detection error: {e}")
        return normalize_result({"is_violation": False, "rationale": f"Error: {e}"})

def encode_alert_button_value(notion_page_id: str | None, **kwargs) -> str:
    """Slackボタンのvalueに埋め込むデータをJSON化"""
    data = kwargs
    if notion_page_id:
        data["notion_page_id"] = notion_page_id
    # Slackのvalue制限(2000文字)に注意
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))