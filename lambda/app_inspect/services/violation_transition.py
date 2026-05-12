from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .models import ModerationResult, severity_rank

ACTIVE_VIOLATION_STATUSES = {"警告済み", "期限超過", "再警告済み"}


@dataclass(frozen=True)
class EditDecision:
    action: Literal[
        "create_new_violation",
        "reply_still_violation",
        "close_by_edit",
        "no_action",
    ]
    current_status: str | None = None


def _extract_select_name(page: dict[str, Any], prop_name: str) -> str:
    props = page.get("properties") or {}
    select_obj = (props.get(prop_name) or {}).get("select") or {}
    name = select_obj.get("name")
    return name if isinstance(name, str) else ""


def decide_edit_transition(
    *,
    existing_page: dict[str, Any] | None,
    result: ModerationResult,
    min_severity_to_alert: str,
) -> EditDecision:
    if existing_page is None:
        if result.is_violation and severity_rank(result.severity) >= severity_rank(min_severity_to_alert):
            return EditDecision(action="create_new_violation")
        return EditDecision(action="no_action")

    current_status = _extract_select_name(existing_page, "対応ステータス")

    if result.is_violation:
        if severity_rank(result.severity) >= severity_rank(min_severity_to_alert):
            return EditDecision(
                action="reply_still_violation",
                current_status=current_status,
            )
        return EditDecision(
            action="no_action",
            current_status=current_status,
        )

    if current_status in ACTIVE_VIOLATION_STATUSES:
        return EditDecision(
            action="close_by_edit",
            current_status=current_status,
        )

    return EditDecision(
        action="no_action",
        current_status=current_status,
    )