from dataclasses import dataclass
from typing import Literal

from .models import ModerationResult

ACTIVE_STATUSES = {"警告済み", "期限超過", "再警告済み"}


@dataclass(frozen=True)
class TransitionDecision:
    action: Literal[
        "create_new_violation",
        "append_edit_notice",
        "close_violation_as_edited",
        "no_action",
    ]
    status: str | None = None


def extract_status(existing_page: dict | None) -> str | None:
    if not existing_page:
        return None

    props = existing_page.get("properties") or {}
    return ((props.get("対応ステータス") or {}).get("select") or {}).get("name")


def decide_transition(
    existing_page: dict | None,
    moderation_result: ModerationResult,
) -> TransitionDecision:
    status = extract_status(existing_page)

    if existing_page is None:
        if moderation_result.is_violation:
            return TransitionDecision(action="create_new_violation", status=None)
        return TransitionDecision(action="no_action", status=None)

    if moderation_result.is_violation:
        return TransitionDecision(action="append_edit_notice", status=status)

    if status in ACTIVE_STATUSES:
        return TransitionDecision(action="close_violation_as_edited", status=status)

    return TransitionDecision(action="no_action", status=status)