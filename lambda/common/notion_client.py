import logging
import re
import requests
from datetime import datetime, timezone
from typing import Optional, Any

logger = logging.getLogger(__name__)

_NOTION_API_BASE = "https://api.notion.com/v1"
_SLACK_LINK_PATTERN = re.compile(r"slack\.com/archives/([A-Z0-9]+)/p(\d+)")
_SLACK_WORKSPACE_PATTERN = re.compile(r"https://([^.]+)\.slack\.com/")


class NotionClient:
    def __init__(self, api_key: str, db_id: str, articles_db_id: str = "",
                 health_db_id: str = ""):
        self.api_key = api_key
        self.db_id = db_id
        self.articles_db_id = articles_db_id
        self.health_db_id = health_db_id
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
                "property": "Message_TS",
                "rich_text": {"equals": message_ts}
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
        team_id: str = None,
    ) -> Optional[str]:
        """違反ログを作成し、Page IDを返す"""
        if not self.db_id:
            return None

        props: dict[str, Any] = {
            "投稿内容": {"title": [{"text": {"content": post_content[:500]}}]},
            "投稿者": {"rich_text": [{"text": {"content": user_id}}]},
            "チャンネル": {"rich_text": [{"text": {"content": channel}}]},
            "検出日時": {"date": {"start": datetime.now().isoformat()}},
            "判定結果": {"select": {"name": result}},
            "検出方法": {"select": {"name": method}},
            "対応ステータス": {"select": {"name": "未対応"}},
        }

        if message_ts:
            props["Message_TS"] = {"rich_text": [{"text": {"content": message_ts}}]}

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

        if confidence is not None:
            props["信頼度"] = {"number": confidence}

        if team_id:
            props["team_id"] = {"rich_text": [{"text": {"content": team_id}}]}

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

    def find_article_page_id(self, article_id: str) -> Optional[str]:
        """条文IDから条文マスタDBのpage_idを検索"""
        if not self.articles_db_id or not article_id:
            return None
        url = f"{_NOTION_API_BASE}/databases/{self.articles_db_id}/query"
        body = {
            "filter": {
                "property": "条文ID",
                "title": {"equals": article_id},
            }
        }
        try:
            resp = requests.post(url, headers=self.headers, json=body, timeout=10)
            if not resp.ok:
                logger.error("Article lookup failed for %s: %s", article_id, resp.status_code)
                return None
            results = resp.json().get("results", [])
            return results[0]["id"] if results else None
        except Exception as e:
            logger.error("Article lookup failed for %s: %s", article_id, e)
            return None

    def get_article_name(self, page_id: str) -> Optional[str]:
        """条文ページのpage_idから条文名（条項番号）を取得する"""
        url = f"{_NOTION_API_BASE}/pages/{page_id}"
        try:
            resp = requests.get(url, headers=self.headers, timeout=10)
            if not resp.ok:
                logger.error("Article page fetch failed for %s: %s", page_id, resp.status_code)
                return None
            props = resp.json().get("properties", {})
            article_texts = props.get("条項番号", {}).get("rich_text", [])
            if article_texts:
                return article_texts[0].get("plain_text")
            title_texts = props.get("条文ID", {}).get("title", [])
            return title_texts[0].get("plain_text") if title_texts else None
        except Exception as e:
            logger.error("Article page fetch failed for %s: %s", page_id, e)
            return None

    def get_page(self, page_id: str) -> Optional[dict]:
        """ページ情報を取得する"""
        url = f"{_NOTION_API_BASE}/pages/{page_id}"
        try:
            resp = requests.get(url, headers=self.headers, timeout=10)
            if not resp.ok:
                logger.error("Page fetch failed for %s: %s", page_id, resp.status_code)
                return None
            return resp.json()
        except Exception as e:
            logger.error("Page fetch failed for %s: %s", page_id, e)
            return None

    def _update_page(self, page_id: str, props: dict[str, Any]) -> bool:
        """ページプロパティを更新する共通メソッド"""
        url = f"{_NOTION_API_BASE}/pages/{page_id}"
        try:
            resp = requests.patch(
                url, headers=self.headers, json={"properties": props}, timeout=5
            )
            if not resp.ok:
                logger.error(f"Page update failed for {page_id}: {resp.status_code} {resp.text}")
                return False
            return True
        except Exception as e:
            logger.error(f"Page update failed for {page_id}: {e}")
            return False

    def update_status(
        self,
        page_id: str,
        status: str,
        warning_sent_at: datetime = None,
        responder_id: str = None,
    ) -> bool:
        """ページのステータスを更新する。初回対応者・警告送信日時も任意で記録。"""
        props: dict[str, Any] = {
            "対応ステータス": {"select": {"name": status}}
        }
        if warning_sent_at:
            props["警告送信日時"] = {"date": {"start": warning_sent_at.isoformat()}}
        if responder_id:
            props["初回対応者"] = {"rich_text": [{"text": {"content": responder_id}}]}

        return self._update_page(page_id, props)

    # ---- Lambda D (app_remind) 用メソッド ----

    def query_approved_unreminded(self) -> list[dict[str, Any]]:
        """Approved（初回警告 + 48h経過チェック対象）のレコードを取得"""
        return self._query({
            "property": "対応ステータス", "select": {"equals": "警告済み"},
        })

    def set_template_relation(self, page_id: str, template_page_id: str) -> bool:
        """使用テンプレートRelationを設定"""
        return self._update_page(page_id, {
            "使用テンプレート": {"relation": [{"id": template_page_id}]},
        })

    def set_article_relation(self, page_id: str, article_page_id: str) -> bool:
        """対象条文Relationを設定"""
        return self._update_page(page_id, {
            "対象条文": {"relation": [{"id": article_page_id}]},
        })

    def mark_48h_over(self, page_id: str) -> bool:
        """対応ステータスを 期限超過 に更新"""
        return self._update_page(page_id, {
            "対応ステータス": {"select": {"name": "期限超過"}},
        })

    def mark_reminded(self, page_id: str) -> bool:
        """対応ステータスを 再警告済み に更新"""
        return self._update_page(page_id, {
            "対応ステータス": {"select": {"name": "再警告済み"}},
        })

    def mark_closed(self, page_id: str, responder_name: str = "") -> bool:
        """対応ステータスを 対応終了 に更新"""
        props: dict[str, Any] = {
            "対応ステータス": {"select": {"name": "対応終了"}},
        }
        if responder_name:
            props["再警告対応者"] = {"rich_text": [{"text": {"content": responder_name}}]}
        return self._update_page(page_id, props)

    def update_admin_notification(
        self, page_id: str, admin_channel_id: str, admin_message_ts: str,
    ) -> bool:
        """管理ch通知のチャンネルIDとメッセージTSを保存"""
        return self._update_page(page_id, {
            "通知チャンネルID": {"rich_text": [{"text": {"content": admin_channel_id}}]},
            "通知メッセージTS": {"rich_text": [{"text": {"content": admin_message_ts}}]},
        })

    def update_rewarn_info(
        self,
        page_id: str,
        rewarn_sent_at: datetime,
        template_page_id: str = "",
        responder_name: str = "",
    ) -> bool:
        """再警告情報を一括更新"""
        props: dict[str, Any] = {
            "対応ステータス": {"select": {"name": "再警告済み"}},
            "再警告送信日時": {"date": {"start": rewarn_sent_at.isoformat()}},
        }
        if template_page_id:
            props["再警告テンプレート"] = {"relation": [{"id": template_page_id}]}
        if responder_name:
            props["再警告対応者"] = {"rich_text": [{"text": {"content": responder_name}}]}
        return self._update_page(page_id, props)

    @staticmethod
    def parse_slack_link(url: Optional[str]) -> Optional[tuple[str, str, Optional[str]]]:
        """Slackパーマリンクから (channel_id, message_ts, workspace) を抽出"""
        if not url:
            return None
        match = _SLACK_LINK_PATTERN.search(url)
        if not match:
            return None
        channel_id = match.group(1)
        raw_ts = match.group(2)
        message_ts = f"{raw_ts[:10]}.{raw_ts[10:]}" if len(raw_ts) > 10 else raw_ts
        ws_match = _SLACK_WORKSPACE_PATTERN.search(url)
        workspace = ws_match.group(1) if ws_match else None
        return channel_id, message_ts, workspace

    @staticmethod
    def extract_reminder_fields(page: dict[str, Any]) -> dict[str, Any]:
        """Notionページからリマインド処理に必要なフィールドを抽出"""
        props = page.get("properties", {})
        post_link = props.get("投稿リンク", {}).get("url")
        warning_date_obj = props.get("警告送信日時", {}).get("date")
        warning_sent_at = None
        if warning_date_obj and warning_date_obj.get("start"):
            warning_sent_at = warning_date_obj["start"]
        poster_texts = props.get("投稿者", {}).get("rich_text", [])
        poster = poster_texts[0]["plain_text"] if poster_texts else None
        title_texts = props.get("投稿内容", {}).get("title", [])
        title = title_texts[0]["plain_text"] if title_texts else ""
        additional_texts = props.get("追加メッセージ", {}).get("rich_text", [])
        additional_message = additional_texts[0]["plain_text"] if additional_texts else ""
        relation_items = props.get("使用テンプレート", {}).get("relation", [])
        template_page_id = relation_items[0]["id"] if relation_items else ""
        article_relation = props.get("対象条文", {}).get("relation", [])
        article_page_id = article_relation[0]["id"] if article_relation else ""
        team_id_texts = props.get("team_id", {}).get("rich_text", [])
        team_id = team_id_texts[0]["plain_text"] if team_id_texts else None
        admin_ch_texts = props.get("通知チャンネルID", {}).get("rich_text", [])
        admin_channel_id = admin_ch_texts[0]["plain_text"] if admin_ch_texts else ""
        admin_ts_texts = props.get("通知メッセージTS", {}).get("rich_text", [])
        admin_message_ts = admin_ts_texts[0]["plain_text"] if admin_ts_texts else ""
        return {
            "page_id": page["id"],
            "post_link": post_link,
            "warning_sent_at": warning_sent_at,
            "poster": poster,
            "title": title,
            "article_page_id": article_page_id,
            "additional_message": additional_message,
            "template_page_id": template_page_id,
            "team_id": team_id,
            "admin_channel_id": admin_channel_id,
            "admin_message_ts": admin_message_ts,
        }

    @staticmethod
    def is_past_threshold(warning_sent_at: Optional[str], hours: int) -> bool:
        """警告送信日時からhours時間経過しているか判定"""
        if not warning_sent_at:
            return False
        try:
            ts = warning_sent_at.replace("Z", "+00:00") if warning_sent_at.endswith("Z") else warning_sent_at
            sent_dt = datetime.fromisoformat(ts)
            if sent_dt.tzinfo is None:
                sent_dt = sent_dt.replace(tzinfo=timezone.utc)
            elapsed_hours = (datetime.now(timezone.utc) - sent_dt).total_seconds() / 3600
            return elapsed_hours >= hours
        except (ValueError, TypeError) as e:
            logger.warning(f"Failed to parse warning_sent_at '{warning_sent_at}': {e}")
            return False

    # ---- ヘルスチェック ----

    def write_health_status(
        self, lambda_name: str, status: str, error_message: str = "",
    ) -> bool:
        """ヘルスチェックDBにLambdaの実行状態を記録（上書き方式）

        Lambda名で既存行を検索し、あれば更新、なければ新規作成。
        """
        if not self.health_db_id:
            return False

        now = datetime.now(timezone.utc).isoformat()
        props: dict[str, Any] = {
            "Lambda名": {"title": [{"text": {"content": lambda_name}}]},
            "ステータス": {"select": {"name": status}},
            "最終実行日時": {"date": {"start": now}},
        }
        if error_message:
            props["エラー内容"] = {
                "rich_text": [{"text": {"content": error_message[:2000]}}],
            }
        else:
            props["エラー内容"] = {"rich_text": []}

        # 既存行を検索
        url = f"{_NOTION_API_BASE}/databases/{self.health_db_id}/query"
        body = {
            "filter": {
                "property": "Lambda名",
                "title": {"equals": lambda_name},
            }
        }
        try:
            resp = requests.post(url, headers=self.headers, json=body, timeout=10)
            if resp.ok:
                results = resp.json().get("results", [])
                if results:
                    # 既存行を更新
                    page_id = results[0]["id"]
                    update_props = {k: v for k, v in props.items() if k != "Lambda名"}
                    return self._update_page(page_id, update_props)

            # 新規作成
            create_url = f"{_NOTION_API_BASE}/pages"
            payload = {
                "parent": {"database_id": self.health_db_id},
                "properties": props,
            }
            resp = requests.post(
                create_url, headers=self.headers, json=payload, timeout=5,
            )
            return resp.ok
        except Exception as e:
            logger.error("Health status write failed for %s: %s", lambda_name, e)
            return False
