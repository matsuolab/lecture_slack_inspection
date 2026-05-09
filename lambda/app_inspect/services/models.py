from dataclasses import dataclass
from typing import Literal

Severity = Literal["low", "medium", "high"]
InspectEventKind = Literal["new_message", "edited_message"]


@dataclass(frozen=True)
class ModerationResult:
    is_violation: bool
    severity: Severity
    categories: list[str]
    rationale: str
    recommended_reply: str
    confidence: float = 0.0
    article_id: str | None = None
    method: str | None = None


@dataclass(frozen=True)
class InspectEvent:
    kind: InspectEventKind
    team_id: str
    channel_id: str
    user_id: str
    message_ts: str
    text: str
    event_ts: str | None = None
    previous_text: str | None = None
    editor_user_id: str | None = None


@dataclass(frozen=True)
class SlackIdentity:
    profile_name: str
    channel_name: str
    workspace_name: str


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
        recommended_reply = (
            "この投稿はガイドラインに抵触する可能性があります。"
            "内容をご確認の上、削除または修正をお願いします。"
        )

    method = raw.get("method")
    if method is not None:
        method = str(method).strip() or None

    return ModerationResult(
        is_violation=is_violation,
        severity=severity,  # type: ignore[arg-type]
        categories=categories,
        rationale=rationale,
        recommended_reply=recommended_reply,
        confidence=raw.get("confidence", 0.0),
        article_id=raw.get("article_id"),
        method=method,
    )


def severity_rank(sev: Severity) -> int:
    return {"low": 0, "medium": 1, "high": 2}[sev]