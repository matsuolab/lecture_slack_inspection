import requests
from datetime import datetime
from typing import Optional, Any

class NotionClient:
    def __init__(self, api_key: str, db_id: str):
        self.api_key = api_key
        self.db_id = db_id
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json"
        }

    def create_violation_log(
        self,
        post_content: str,
        user_id: str,
        channel: str,
        result: str,
        method: str,
        reason: str = None,
        post_link: str = None,
        article_id: str = None,
        confidence: float = None,
    ) -> Optional[str]:
        """違反ログを作成し、Page IDを返す"""
        if not self.db_id:
            return None

        # タイトル長すぎ対策
        title = post_content[:100]

        props: dict[str, Any] = {
            "投稿内容": {"title": [{"text": {"content": title}}]},
            "投稿者": {"rich_text": [{"text": {"content": user_id}}]},
            "チャンネル": {"rich_text": [{"text": {"content": channel}}]},
            "検出日時": {"date": {"start": datetime.now().isoformat()}},
            "判定結果": {"select": {"name": result}},
            "検出方法": {"select": {"name": method}},
            "対応ステータス": {"select": {"name": "Unprocessed"}},
        }

        if post_link:
            props["投稿リンク"] = {"url": post_link}

        if article_id:
            props["該当条文"] = {"rich_text": [{"text": {"content": article_id}}]}
        
        if confidence is not None:
            props["信頼度"] = {"number": confidence}

        # 理由を投稿者欄に追記（Notionのプロパティ構成に合わせて調整可）
        if reason:
            props["投稿者"]["rich_text"].append({"text": {"content": f" | 理由: {reason[:100]}"}})

        url = "https://api.notion.com/v1/pages"
        payload = {
            "parent": {"database_id": self.db_id},
            "properties": props
        }

        try:
            resp = requests.post(url, headers=self.headers, json=payload, timeout=5)

            if not resp.ok:
                print(f"[Notion] Create failed: {resp.status_code} {resp.text}")
                return None
            
            resp.raise_for_status()
            return resp.json().get("id")
        except Exception as e:
            print(f"[Notion] Create failed: {e}")
            return None

    def update_status(self, page_id: str, status: str) -> bool:
        """ステータス更新"""
        url = f"https://api.notion.com/v1/pages/{page_id}"
        payload = {
            "properties": {
                "対応ステータス": {"select": {"name": status}}
            }
        }
        try:
            resp = requests.patch(url, headers=self.headers, json=payload, timeout=5)
            resp.raise_for_status()
            return True
        except Exception as e:
            print(f"[Notion] Update failed: {e}")
            return None