"""Notion API"""
import logging
import requests
from datetime import datetime

logger = logging.getLogger(__name__)

_config = {"api_key": None, "db_id": None, "headers": None}
NOTION_VERSION = "2022-06-28"


def init_notion_client(api_key: str, violation_log_db_id: str):
    _config["api_key"] = api_key
    _config["db_id"] = violation_log_db_id
    _config["headers"] = {
        "Authorization": f"Bearer {api_key}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json"
    }


def _query(database_id: str, filter_obj: dict = None) -> list:
    url = f"https://api.notion.com/v1/databases/{database_id}/query"
    results = []
    cursor = None

    while True:
        body = {"filter": filter_obj} if filter_obj else {}
        if cursor:
            body["start_cursor"] = cursor

        resp = requests.post(url, headers=_config["headers"], json=body)
        if resp.status_code != 200:
            raise Exception(f"Notion API error: {resp.status_code} - {resp.text}")

        data = resp.json()
        results.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")

    return results


def _create_page(database_id: str, properties: dict) -> str:
    url = "https://api.notion.com/v1/pages"
    body = {"parent": {"database_id": database_id}, "properties": properties}
    resp = requests.post(url, headers=_config["headers"], json=body)
    if resp.status_code != 200:
        raise Exception(f"Notion API error: {resp.status_code} - {resp.text}")
    return resp.json()["id"]


def check_duplicate_violation(message_ts: str) -> bool:
    """同じ投稿が既に記録されているかチェック"""
    if not _config["db_id"]:
        return False
    try:
        results = _query(_config["db_id"], {
            "property": "投稿内容",
            "title": {"starts_with": f"{message_ts}:"}
        })
        return len(results) > 0
    except Exception:
        return False


def create_violation_log(
    post_id: str, post_content: str, user_id: str, channel: str,
    result: str, method: str, article_id: str = None,
    confidence: float = None, reason: str = None, post_link: str = None,
) -> str:
    """違反ログを記録"""
    if not _config["db_id"]:
        raise Exception("Notion client not initialized")

    title = f"{post_id}: {post_content[:100]}"
    props = {
        "投稿内容": {"title": [{"text": {"content": title[:200]}}]},
        "投稿者": {"rich_text": [{"text": {"content": user_id}}]},
        "チャンネル": {"rich_text": [{"text": {"content": channel}}]},
        "検出日時": {"date": {"start": datetime.now().isoformat()}},
        "判定結果": {"select": {"name": result}},
        "検出方法": {"select": {"name": method}},
        "対応済み": {"checkbox": False},
        "対応ステータス": {"select": {"name": "未対応"}},
        "リマインド送信済": {"checkbox": False},
    }

    if article_id:
        props["該当条文"] = {"rich_text": [{"text": {"content": article_id}}]}
    if confidence is not None:
        props["確信度"] = {"number": confidence}
    if post_link:
        props["投稿リンク"] = {"url": post_link}
    if reason:
        props["投稿者"]["rich_text"][0]["text"]["content"] = f"{user_id} | 理由: {reason[:100]}"

    return _create_page(_config["db_id"], props)


def query_by_status(status: str) -> list:
    """指定ステータスの違反ログを取得"""
    if not _config["db_id"]:
        return []

    try:
        results = _query(_config["db_id"], {
            "property": "対応ステータス",
            "select": {"equals": status}
        })
        return _parse_violation_logs(results)
    except Exception:
        return []


def _parse_violation_logs(results: list) -> list:
    """Notion結果をパース"""
    logs = []
    for page in results:
        props = page["properties"]
        logs.append({
            "page_id": page["id"],
            "post_content": _get_title(props.get("投稿内容", {})),
            "user_id": _get_text(props.get("投稿者", {})),
            "channel": _get_text(props.get("チャンネル", {})),
            "category": _get_select(props.get("判定結果", {})),
            "article_id": _get_text(props.get("該当条文", {})),
            "post_link": props.get("投稿リンク", {}).get("url"),
            "status": _get_select(props.get("対応ステータス", {})),
        })
    return logs


def _get_title(prop: dict) -> str:
    if prop.get("title") and len(prop["title"]) > 0:
        return prop["title"][0].get("plain_text", "")
    return ""


def _get_text(prop: dict) -> str:
    if prop.get("rich_text") and len(prop["rich_text"]) > 0:
        return prop["rich_text"][0].get("plain_text", "")
    return ""


def _get_select(prop: dict) -> str:
    if prop.get("select"):
        return prop["select"].get("name", "")
    return ""


def update_violation_status(page_id: str, status: str, warning_sent_at: datetime = None) -> bool:
    """違反ログのステータスを更新"""
    url = f"https://api.notion.com/v1/pages/{page_id}"

    props = {"対応ステータス": {"select": {"name": status}}}
    if warning_sent_at:
        props["警告送信日時"] = {"date": {"start": warning_sent_at.isoformat()}}

    try:
        resp = requests.patch(url, headers=_config["headers"], json={"properties": props})
        if resp.status_code != 200:
            logger.error(f"Failed to update status: {resp.status_code} - {resp.text}")
            return False
        return True
    except Exception as e:
        logger.error(f"Exception updating status: {e}")
        return False
