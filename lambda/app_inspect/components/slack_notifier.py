from __future__ import annotations

from typing import Any

from common.observability import log_error
from common.template_manager import get_template_options

from .slack_builder import encode_alert_button_value
from .slack_ui import build_violation_alert_blocks
from ..services.models import ModerationResult, SlackIdentity


def _as_non_empty_str(value: object) -> str | None:
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return None


def _truncate_text(text: str, limit: int = 180) -> str:
    normalized = " ".join((text or "").split())
    if not normalized:
        return "（空）"
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1] + "…"


def _load_warning_template_options(context: Any, cfg: Any) -> list[tuple[str, str]] | None:
    notion_template_db_id = _as_non_empty_str(getattr(cfg, "notion_template_db_id", None))
    if not notion_template_db_id:
        return None

    try:
        raw_options = get_template_options(cfg.notion_api_key, notion_template_db_id)
        if not raw_options:
            return None

        options = [
            (o["name"], o["page_id"])
            for o in raw_options
            if o.get("usage") == "警告"
        ]
        return options or None
    except Exception as e:
        log_error(context, action="fetch_template_options", error=e)
        return None


def send_new_violation_alert(
    *,
    context: Any,
    slack_client: Any,
    cfg: Any,
    trace_id: str,
    identity: SlackIdentity,
    raw_user_id: str,
    raw_channel_id: str,
    message_ts: str,
    post_text: str,
    post_link: str | None,
    notion_page_id: str | None,
    article_display_name: str | None,
    result: ModerationResult,
) -> dict:
    button_value = encode_alert_button_value(
        notion_page_id=_as_non_empty_str(notion_page_id),
        trace_id=trace_id,
        origin_channel=raw_channel_id,
        origin_ts=message_ts,
        reason=result.rationale,
        article_id=article_display_name,
    )

    if cfg.use_mock_openai:
        detection_method = "Mock"
        confidence_for_ui = None
    else:
        raw_method = getattr(result, "method", None)
        detection_method = raw_method if isinstance(raw_method, str) and raw_method else "LLM"
        confidence_for_ui = result.confidence if detection_method == "LLM" else None

    template_options = _load_warning_template_options(context, cfg)

    blocks = build_violation_alert_blocks(
        author_user_id=(raw_user_id or None),
        author_display=(identity.profile_name or None),
        origin_channel_id=raw_channel_id,
        post_link=post_link,
        post_ts=message_ts,
        post_text=post_text,
        detection_method=detection_method,
        confidence=confidence_for_ui,
        rationale=result.rationale,
        guideline_article=article_display_name,
        categories=result.categories,
        button_value=button_value,
        warning_template_options=template_options,
    )

    return slack_client.chat_postMessage(
        channel=cfg.alert_private_channel_id,
        text="〖違反検知アラート〗",
        blocks=blocks,
    )


def send_edit_still_violation_reply(
    *,
    slack_client: Any,
    admin_channel_id: str,
    admin_message_ts: str,
    previous_text: str,
    current_text: str,
    post_link: str | None,
) -> None:
    link_line = f"\n投稿リンク: {post_link}" if post_link else ""
    slack_client.chat_postMessage(
        channel=admin_channel_id,
        thread_ts=admin_message_ts,
        text=(
            "投稿が編集されました。"
            f"{link_line}"
        ),
    )


def send_edit_closed_reply(
    *,
    slack_client: Any,
    admin_channel_id: str,
    admin_message_ts: str,
    previous_text: str,
    current_text: str,
    post_link: str | None,
) -> None:
    link_line = f"\n投稿リンク: {post_link}" if post_link else ""
    slack_client.chat_postMessage(
        channel=admin_channel_id,
        thread_ts=admin_message_ts,
        text=(
            "投稿が編集され、違反ではなくなったため対応終了にしました。"
            f"{link_line}"
        ),
    )