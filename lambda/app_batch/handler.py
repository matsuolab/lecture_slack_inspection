"""Lambda E (app_batch) — 過去メッセージ一括スキャン

CloudShell から `aws lambda invoke` で起動する。
詳細: AIE09BOT_experiments/docs/batch_scan_plan.md
"""
import time
from typing import Any, Optional
from slack_sdk import WebClient
from openai import OpenAI

from common.observability import build_context, log_info, log_error, emit_metric, Timer
from common.notion_client import NotionClient
from app_inspect.services.moderation import run_moderation
from app_inspect.services.models import severity_rank, ModerationResult
from .services.config import load_config
from .services.scanner import iter_channel_messages

SERVICE = "app_batch"


def lambda_handler(event: dict, context: Any) -> dict:
    ctx = build_context(event, context, service=SERVICE)
    timer = Timer()
    log_info(ctx, action="batch_start", payload=event)

    try:
        team_id = event.get("team_id")
        channel_id = event.get("channel_id")
        oldest = event.get("oldest")
        latest = event.get("latest")
        dry_run = bool(event.get("dry_run", False))

        if not team_id or not channel_id:
            return _error("team_id and channel_id are required")

        cfg = load_config(team_id)
        slack = WebClient(token=cfg.slack_bot_token)
        openai_client = None if cfg.use_mock_openai else OpenAI(api_key=cfg.openai_api_key)
        notion = NotionClient(cfg.notion_api_key, cfg.notion_db_id, cfg.notion_articles_db_id) if not dry_run else None

        scanned = 0
        violations = 0
        duplicates = 0
        next_oldest: Optional[str] = None

        for msg in iter_channel_messages(slack, channel_id, oldest=oldest, latest=latest, sleep_ms=cfg.sleep_ms):
            if scanned >= cfg.max_messages_per_invoke:
                next_oldest = msg.get("ts")
                log_info(ctx, action="max_reached", next_oldest=next_oldest)
                break

            scanned += 1
            text = msg.get("text", "").strip()
            if not text:
                continue

            result = _moderate(cfg, openai_client, text)
            if not result.is_violation:
                continue

            violations += 1

            if dry_run:
                log_info(
                    ctx,
                    action="dry_run_violation",
                    ts=msg.get("ts"),
                    text=text[:200],
                    severity=result.severity,
                    article_id=result.article_id,
                    rationale=result.rationale[:200] if result.rationale else None,
                )
                continue

            if notion.check_duplicate_violation(msg["ts"]):
                duplicates += 1
                continue

            _record_violation(notion, slack, cfg, team_id, channel_id, msg, result)

            if cfg.sleep_ms > 0:
                time.sleep(cfg.sleep_ms / 1000.0)

        emit_metric(ctx, "BatchScanned", scanned)
        emit_metric(ctx, "BatchViolations", violations)
        emit_metric(ctx, "BatchDuplicates", duplicates)
        emit_metric(ctx, "BatchTotalMs", timer.ms(), unit="Milliseconds")

        result = {
            "ok": True,
            "scanned": scanned,
            "violations": violations,
            "duplicates": duplicates,
            "dry_run": dry_run,
            "next_oldest": next_oldest,
        }
        log_info(ctx, action="batch_done", **result)
        return result

    except Exception as e:
        log_error(ctx, action="batch_handler", error=e)
        return _error(str(e))


def _moderate(cfg, openai_client, text: str) -> ModerationResult:
    if cfg.use_mock_openai:
        is_mock = "違反" in text
        return ModerationResult(
            is_violation=is_mock,
            severity="medium",
            categories=["mock_test"],
            rationale="[MOCK] バッチスキャン",
            recommended_reply="",
            confidence=0.9,
            article_id="mock_article_123",
            method="バッチLLM",
        )
    result = run_moderation(
        openai_client,
        cfg.openai_model,
        cfg.guidelines_text,
        text,
        cfg.openai_embedding_model,
    )
    if result.is_violation:
        result = ModerationResult(
            is_violation=result.is_violation,
            severity=result.severity,
            categories=result.categories,
            rationale=result.rationale,
            recommended_reply=result.recommended_reply,
            confidence=result.confidence,
            article_id=result.article_id,
            method="バッチLLM",
        )
    return result


def _record_violation(notion, slack, cfg, team_id, channel_id, msg, result):
    raw_user_id = msg.get("user", "")
    profile_name = f"@{raw_user_id}"
    channel_name = channel_id
    workspace_name = team_id
    post_link = None

    try:
        if raw_user_id:
            u_res = slack.users_info(user=raw_user_id)
            p = u_res["user"]["profile"]
            profile_name = "@" + (p.get("display_name") or p.get("real_name") or raw_user_id)
        c_res = slack.conversations_info(channel=channel_id)
        channel_name = c_res["channel"]["name"]
        t_res = slack.team_info(team=team_id)
        workspace_name = t_res["team"]["name"]
        permalink_resp = slack.chat_getPermalink(channel=channel_id, message_ts=msg["ts"])
        post_link = permalink_resp.get("permalink")
    except Exception:
        pass

    page_id = notion.create_violation_log(
        post_content=msg.get("text", ""),
        user_id=profile_name,
        channel=channel_name,
        workspace=workspace_name,
        result="Violation",
        method="バッチLLM",
        reason=result.rationale,
        severity=result.severity,
        categories=result.categories,
        post_link=post_link,
        article_id=result.article_id,
        confidence=result.confidence,
        message_ts=msg["ts"],
        team_id=team_id,
    )

    if page_id and result.article_id:
        article_page_id = notion.find_article_page_id(result.article_id)
        if article_page_id:
            notion.set_article_relation(page_id, article_page_id)


def _error(msg: str) -> dict:
    return {"ok": False, "error": msg}
