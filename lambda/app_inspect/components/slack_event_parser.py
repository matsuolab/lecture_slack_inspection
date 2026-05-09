from __future__ import annotations

from ..services.models import InspectEvent


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def parse_slack_message_event(body_json: dict, default_team_id: str = "") -> InspectEvent | None:
    if body_json.get("type") != "event_callback":
        return None

    ev = body_json.get("event") or {}
    if ev.get("type") != "message":
        return None

    team_id = str(body_json.get("team_id") or default_team_id or "")
    if not team_id:
        return None

    subtype = ev.get("subtype")

    if not subtype:
        if ev.get("bot_id"):
            return None

        text = _clean_text(ev.get("text"))
        if not text:
            return None

        return InspectEvent(
            kind="new_message",
            team_id=team_id,
            channel_id=str(ev.get("channel", "") or ""),
            user_id=str(ev.get("user", "") or ""),
            message_ts=str(ev.get("ts", "") or ""),
            text=text,
            event_ts=str(ev.get("event_ts", "") or "") or None,
        )

    if subtype == "message_changed":
        message = ev.get("message") or {}
        previous_message = ev.get("previous_message") or {}

        if message.get("bot_id") or previous_message.get("bot_id"):
            return None

        text = _clean_text(message.get("text"))
        previous_text = _clean_text(previous_message.get("text"))
        if not text:
            return None

        # 本文が変わっていないメタデータ更新は無視
        if text == previous_text:
            return None

        return InspectEvent(
            kind="edited_message",
            team_id=team_id,
            channel_id=str(ev.get("channel") or message.get("channel") or ""),
            user_id=str(message.get("user") or previous_message.get("user") or ""),
            message_ts=str(message.get("ts") or previous_message.get("ts") or ""),
            text=text,
            previous_text=previous_text,
            editor_user_id=str((message.get("edited") or {}).get("user") or "") or None,
            event_ts=str(ev.get("event_ts", "") or "") or None,
        )

    return None