"""ヘルスチェック共通モジュール: 各Lambdaから呼び出してNotionに実行状態を記録"""

import logging
import os

from common.notion_client import NotionClient

logger = logging.getLogger(__name__)

_HEALTH_DISPLAY_NAMES = {
    "app_inspect": "A: 違反検出",
    "app_alert": "B: Slackボタン処理",
    "app_remind": "D: 定期チェック",
}


def write_health(service: str, status: str, error_message: str = "",
                 notion_api_key: str = ""):
    """ヘルスチェックをNotionに書き込む（失敗しても本体処理を壊さない）"""
    try:
        health_db_id = os.getenv("NOTION_HEALTH_DB_ID", "")
        if not health_db_id:
            return
        if not notion_api_key:
            from common.secret_manager import get_secret
            notion_api_key = get_secret("NOTION_API_KEY_PARAM_NAME")
        display_name = _HEALTH_DISPLAY_NAMES.get(service, service)
        notion = NotionClient(notion_api_key, "", health_db_id=health_db_id)
        notion.write_health_status(display_name, status, error_message)
    except Exception:
        logger.debug("Health check write failed for %s", service, exc_info=True)
