import logging
import requests
from datetime import datetime
from typing import Optional, Any

logger = logging.getLogger(__name__)


class NotionClient:
    def __init__(self, api_key: str, db_id: str):
        self.api_key = api_key
        self.db_id = db_id
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json"
        }

    def _query(self, filter_obj: dict = None) -> list:
        """Notion DBクエリ（ページネーション対応）"""
        url = f"https://api.notion.com/v1/databases/{self.db_id}/query"
        results = []
        cursor = None

        while True:
            body = {}
            if filter_obj:
                body["filter"] = filter_obj
            if cursor:
                body["start_cursor"] = cursor

            resp = requests.post(url, headers=self.headers, json=body, timeout=10)
            if not resp.ok:
                raise Exception(f"Notion query error: {resp.status_code} {resp.text}")

            data = resp.json()
            results.extend(data.get("results", []))
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")

        return results

    def check_duplicate_violation(self, message_ts: str) -> bool:
        """同じ投稿(message_ts)が既に記録されているかチェック"""
        if not self.db_id:
            return False
        try:
            results = self._query({
                "property": "投稿内容",
                "title": {"starts_with": f"{message_ts}:"}
            })
            return len(results) > 0
        except Exception as e:
            logger.error(f"Duplicate check failed: {e}")
            return False

    def create_violation_log(
        self,
        post_content: str,
        user_id: str,
        channel: str,
        result: str,
        method: str,
        reason: str = None,
        severity: str = None,          
        categories: list[str] = None,
        workspace: str = None,
        post_link: str = None,
        article_id: str = None,
        confidence: float = None,
        message_ts: str = None,
    ) -> Optional[str]:
        """違反ログを作成し、Page IDを返す"""
        if not self.db_id:
            return None

        # タイトルにmessage_tsを含めて重複チェック可能に
        content_preview = post_content[:80]
        if message_ts:
            title = f"{message_ts}: {content_preview}"
        else:
            title = content_preview[:100]

        props: dict[str, Any] = {
            "投稿内容": {"title": [{"text": {"content": title[:200]}}]},
            "投稿者": {"rich_text": [{"text": {"content": user_id}}]},
            "チャンネル": {"rich_text": [{"text": {"content": channel}}]},
            "検出日時": {"date": {"start": datetime.now().isoformat()}},
            "判定結果": {"select": {"name": result}},
            "検出方法": {"select": {"name": method}},
            "対応ステータス": {"select": {"name": "Unprocessed"}},
        }

        if workspace:
            props["ワークスペース"] = {"rich_text": [{"text": {"content": workspace}}]}

        if reason:
            props["違反理由"] = {"rich_text": [{"text": {"content": reason[:2000]}}]}
        
        if severity:
            props["重大度"] = {"select": {"name": severity}}
            
        if categories:
            props["違反カテゴリ"] = {"multi_select": [{"name": cat} for cat in categories]}

        if post_link:
            props["投稿リンク"] = {"url": post_link}

        if article_id:
            props["該当条文"] = {"rich_text": [{"text": {"content": article_id}}]}

        if confidence is not None:
            props["信頼度"] = {"number": confidence}

        url = "https://api.notion.com/v1/pages"
        payload = {
            "parent": {"database_id": self.db_id},
            "properties": props
        }

        try:
            resp = requests.post(url, headers=self.headers, json=payload, timeout=5)

            if not resp.ok:
                logger.error(f"Create failed: {resp.status_code} {resp.text}")
                return None

            return resp.json().get("id")
        except Exception as e:
            logger.error(f"Create failed: {e}")
            return None

    def update_status(
        self,
        page_id: str,
        status: str,
        warning_sent_at: datetime = None,
        responder_id: str = None,
    ) -> bool:
        """ページのステータスを更新する。対応者・警告送信日時も任意で記録。"""
        url = f"https://api.notion.com/v1/pages/{page_id}"

        props: dict[str, Any] = {
            "対応ステータス": {"select": {"name": status}}
        }
        if warning_sent_at:
            props["警告送信日時"] = {"date": {"start": warning_sent_at.isoformat()}}
        if responder_id:
            props["対応者"] = {"rich_text": [{"text": {"content": responder_id}}]}

        try:
            resp = requests.patch(
                url, headers=self.headers, json={"properties": props}, timeout=5
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error(f"Update failed: {e}")
            return False
