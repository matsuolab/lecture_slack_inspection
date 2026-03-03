"""警告テンプレート管理: Notion DBからテンプレートを取得し、キャッシュ+プレースホルダー置換"""

import logging
import time
from typing import Any, Optional

import requests

logger = logging.getLogger(__name__)

_NOTION_API_BASE = "https://api.notion.com/v1"
_CACHE_TTL_SECONDS = 30 * 60  # 30分

# モジュールレベルキャッシュ（Warm Start間で保持）
_template_cache: dict[str, dict[str, str]] = {}
_cache_loaded_at: float = 0.0

FALLBACK_TEMPLATES: dict[str, str] = {
    "警告": (
        ":warning: *ガイドライン違反の通知*\n\n"
        "この投稿はコミュニティガイドラインに抵触する可能性があるため、"
        "投稿の削除または修正をお願いします。\n"
        "ご不明点がありましたら管理者までお問い合わせください。"
    ),
    "リマインド": (
        ":bell: *削除リマインド*\n\n"
        "この投稿はコミュニティガイドラインに抵触する可能性があるため、"
        "削除のお願いをしておりました。\n\n"
        "まだ投稿が残っているようですので、ご確認・削除をお願いいたします。\n"
        "ご不明点がありましたら管理者までお問い合わせください。"
    ),
}


def _fetch_templates(api_key: str, db_id: str) -> list[dict[str, Any]]:
    """テンプレートDBから有効なレコードを取得"""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }
    url = f"{_NOTION_API_BASE}/databases/{db_id}/query"
    body = {
        "filter": {"property": "有効", "checkbox": {"equals": True}},
    }
    resp = requests.post(url, headers=headers, json=body, timeout=10)
    if not resp.ok:
        raise Exception(f"Template DB query failed: {resp.status_code} {resp.text}")
    return resp.json().get("results", [])


def _parse_template(page: dict[str, Any]) -> Optional[dict[str, str]]:
    """Notionページからテンプレート情報を抽出"""
    props = page.get("properties", {})

    name_parts = props.get("テンプレート名", {}).get("title", [])
    name = name_parts[0]["plain_text"].strip() if name_parts else None

    usage_obj = props.get("用途", {}).get("select")
    usage = usage_obj["name"] if usage_obj else None

    body_parts = props.get("本文", {}).get("rich_text", [])
    body = "".join(part["plain_text"] for part in body_parts) if body_parts else ""

    if not name or not usage or not body:
        return None

    return {"name": name, "usage": usage, "body": body}


def get_templates(api_key: str, db_id: str, force_refresh: bool = False) -> dict[str, dict[str, str]]:
    """テンプレートを取得（キャッシュ付き）

    Returns:
        {"警告": {"name": "標準警告", "body": "..."}, "リマインド": {"name": "...", "body": "..."}}
    """
    global _template_cache, _cache_loaded_at

    now = time.time()
    cache_valid = _template_cache and (now - _cache_loaded_at < _CACHE_TTL_SECONDS)

    if cache_valid and not force_refresh:
        return _template_cache

    if not db_id:
        return {}

    try:
        pages = _fetch_templates(api_key, db_id)
        templates: dict[str, dict[str, str]] = {}
        for page in pages:
            parsed = _parse_template(page)
            if parsed:
                templates[parsed["usage"]] = {"name": parsed["name"], "body": parsed["body"]}

        _template_cache = templates
        _cache_loaded_at = now
        logger.info("Template cache refreshed: %d templates", len(templates))
        return _template_cache

    except Exception as e:
        logger.error("Failed to fetch templates: %s", e)
        if _template_cache:
            logger.warning("Using stale template cache")
            return _template_cache
        return {}


def get_template_by_page_id(api_key: str, page_id: str) -> Optional[str]:
    """Relation指定されたテンプレートのpage_idから本文を取得"""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Notion-Version": "2022-06-28",
    }
    url = f"{_NOTION_API_BASE}/pages/{page_id}"
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        if not resp.ok:
            logger.error("Failed to fetch template page %s: %s", page_id, resp.status_code)
            return None
        page = resp.json()
        parsed = _parse_template(page)
        if parsed and parsed["body"]:
            return parsed["body"]
        return None
    except Exception as e:
        logger.error("Failed to fetch template page %s: %s", page_id, e)
        return None


def resolve_template(
    usage: str,
    api_key: str = "",
    db_id: str = "",
    template_page_id: str = "",
) -> str:
    """用途に応じたテンプレート本文を返す（フォールバック付き）

    優先順位:
    1. template_page_id（Relation指定） → そのテンプレートの本文
    2. db_id（テンプレートDB） → 用途に一致するテンプレート
    3. FALLBACK_TEMPLATES（ハードコード）
    """
    # 1. Relation指定のテンプレート
    if template_page_id and api_key:
        body = get_template_by_page_id(api_key, template_page_id)
        if body:
            return body
        logger.warning("Relation template not found, falling back: %s", template_page_id)

    # 2. テンプレートDBから用途で検索
    if api_key and db_id:
        templates = get_templates(api_key, db_id)
        if usage in templates:
            return templates[usage]["body"]

    # 3. ハードコードフォールバック
    return FALLBACK_TEMPLATES.get(usage, FALLBACK_TEMPLATES["警告"])


def render_template(body: str, **kwargs: str) -> str:
    """プレースホルダーを置換。未定義は残す。"""
    for key, value in kwargs.items():
        body = body.replace(f"{{{{{key}}}}}", str(value))
    return body


def compose_message(template_body: str, additional_message: str = "") -> str:
    """テンプレート + 追加メッセージを結合"""
    message = template_body
    if additional_message and additional_message.strip():
        message += "\n\n" + additional_message.strip()
    return message
