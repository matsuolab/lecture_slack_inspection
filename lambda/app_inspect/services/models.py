from dataclasses import dataclass
from typing import Literal

Severity = Literal["low", "medium", "high"]

@dataclass(frozen=True)
class ModerationResult:
    is_violation: bool
    severity: Severity
    categories: list[str]
    rationale: str
    recommended_reply: str

def normalize_result(raw: dict) -> ModerationResult:
    is_violation = bool(raw.get("is_violation", False))
    severity = str(raw.get("severity", "low")).lower()
    if severity not in ("low", "medium", "high"):
        severity = "low"
    categories = raw.get("categories") or []
    if not isinstance(categories, list):
        categories = [str(categories)]
    categories = [str(x) for x in categories][:8]
    rationale = str(raw.get("rationale", ""))[:800]
    recommended_reply = str(raw.get("recommended_reply", "")).strip()[:600]
    if not recommended_reply:
        recommended_reply = "この投稿はガイドラインに抵触する可能性があります。内容をご確認の上、削除または修正をお願いします。"
    return ModerationResult(
        is_violation=is_violation,
        severity=severity,  # type: ignore
        categories=categories,
        rationale=rationale,
        recommended_reply=recommended_reply,
    )

def severity_rank(sev: Severity) -> int:
    return {"low": 0, "medium": 1, "high": 2}[sev]
