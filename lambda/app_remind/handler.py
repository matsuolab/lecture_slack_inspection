"""Lambda C (app_remind): EventBridgeトリガーで削除リマインドを送信する"""

import os
from typing import Any

from slack_sdk import WebClient

from common.observability import build_context, log_info, log_error, emit_metric, Timer
from common.notion_client import NotionClient
from common.secret_manager import get_secret
from .services.config import load_config
from .services.reminder import process_reminders

SERVICE = "app_remind"


def _build_workspace_clients() -> dict[str, WebClient]:
    """SLACK_BOT_TOKEN__{workspace}_PARAM_NAME 形式のSSMパラメータから
    ワークスペース別クライアントを構築する"""
    workspace_clients: dict[str, WebClient] = {}

    for key in os.environ:
        if key.startswith("SLACK_BOT_TOKEN__") and key.endswith("_PARAM_NAME"):
            workspace = key.removeprefix("SLACK_BOT_TOKEN__").removesuffix("_PARAM_NAME").lower()
            token = get_secret(key)
            if token:
                workspace_clients[workspace] = WebClient(token=token)

    return workspace_clients


def lambda_handler(event: dict, context: Any) -> dict:
    ctx = build_context(event, context, service=SERVICE)
    total_timer = Timer()
    log_info(ctx, action="request_received")

    try:
        cfg = load_config()

        slack = WebClient(token=cfg.slack_bot_token)
        workspace_clients = _build_workspace_clients()

        notion = NotionClient(cfg.notion_api_key, cfg.notion_db_id)

        stats = process_reminders(
            slack=slack,
            notion=notion,
            hours_threshold=cfg.hours_threshold,
            slack_clients=workspace_clients,
        )

        elapsed_ms = total_timer.ms()
        log_info(ctx, action="completed", stats=stats, elapsed_ms=round(elapsed_ms, 1))
        emit_metric(ctx, "TotalLatencyMs", elapsed_ms, unit="Milliseconds")

        return {"statusCode": 200, "body": "ok"}

    except Exception as e:
        log_error(ctx, action="handler_failed", error=e)
        emit_metric(ctx, "RemindError", 1)
        return {"statusCode": 200, "body": "error_handled"}
