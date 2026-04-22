from typing import Any

from common.template_manager import get_template_options

from ..components.slack_builder import encode_alert_button_value
from ..components.slack_ui import build_violation_alert_blocks
from .models import ModerationResult


def _truncate_for_quote(text: str, limit: int = 180) -> str:
    normalized = " ".join((text or "").split())
    if len(normalized) <= limit:
        return normalized or "（空）"
    return normalized[: limit - 1] + "…"


def _load_warning_template_options(cfg: Any) -> list[tuple[str, str]] | None:
    notion_template_db_id = getattr(cfg, "notion_template_db_id", "")
    if not notion_template_db_id:
        return None

    try:
        raw_options = get_template_options(cfg.notion_api_key, notion_template_db_id)
    except Exception:
        return None

    if not raw_options:
        return None

    return [
        (option["name"], option["page_id"])
        for option in raw_options
        if option.get("usage") == "警告"
    ] or None


def send_new_violation_alert(
    *,
    slack_client: Any,
    notion: Any,
    cfg: Any,
    context: Any,
    raw_user_id: str,
    raw_channel_id: str,
    message_ts: str,
    post_text: str,
    post_link: str | None,
    notion_page_id: str | None,
    article_display_name: str | None,
    result: ModerationResult,
    profile_name: str | None,
) -> dict:
    button_value = encode_alert_button_value(
        notion_page_id=notion_page_id,
        trace_id=context.trace_id,
        origin_channel=raw_channel_id,
        origin_ts=message_ts,
        reason=result.rationale,
        article_id=article_display_name or result.article_id,
    )

    detection_method = getattr(result, "method", None) or "LLM"
    confidence_for_ui = result.confidence if detection_method == "LLM" else None
    if getattr(cfg, "use_mock_openai", False):
        detection_method = "Mock"
        confidence_for_ui = None

    template_options = _load_warning_template_options(cfg)

    blocks = build_violation_alert_blocks(
        author_user_id=(raw_user_id or None),
        author_display=(profile_name or None),
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

    alert_resp = slack_client.chat_postMessage(
        channel=cfg.alert_private_channel_id,
        text="〖違反検知アラート〗",
        blocks=blocks,
    )

    if notion_page_id and alert_resp.get("ok"):
        notion.update_admin_notification(
            notion_page_id,
            admin_channel_id=cfg.alert_private_channel_id,
            admin_message_ts=alert_resp["ts"],
        )

    return alert_resp


def post_edit_notice_to_thread(
    *,
    slack_client: Any,
    admin_channel_id: str,
    admin_message_ts: str,
    post_link: str | None,
    previous_text: str | None,
    current_text: str,
) -> None:
    old_text = _truncate_for_quote(previous_text or "")
    new_text = _truncate_for_quote(current_text)
    link_line = f"\n投稿リンク: {post_link}" if post_link else ""

    slack_client.chat_postMessage(
        channel=admin_channel_id,
        thread_ts=admin_message_ts,
        text=(
            "投稿が編集されました。"
            f"\n編集前: {old_text}"
            f"\n編集後: {new_text}"
            f"{link_line}"
        ),
    )


def post_closed_by_edit_notice(
    *,
    slack_client: Any,
    admin_channel_id: str,
    admin_message_ts: str,
    post_link: str | None,
    previous_text: str | None,
    current_text: str,
) -> None:
    old_text = _truncate_for_quote(previous_text or "")
    new_text = _truncate_for_quote(current_text)
    link_line = f"\n投稿リンク: {post_link}" if post_link else ""

    slack_client.chat_postMessage(
        channel=admin_channel_id,
        thread_ts=admin_message_ts,
        text=(
            "投稿が編集され、違反ではなくなったため対応終了にしました。"
            f"\n編集前: {old_text}"
            f"\n編集後: {new_text}"
            f"{link_line}"
        ),
    )