"""Lambda D (app_remind) ユニットテスト"""

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

from common.notion_client import NotionClient
from app_remind.services.reminder import (
    check_message_exists,
    send_warning,
    send_reminder,
    process_reminders,
    process_remind_requests,
    _resolve_slack_client,
)
from common.template_manager import (
    FALLBACK_TEMPLATES,
    resolve_template,
    get_template_by_page_id,
)


# ---- NotionClient static methods ----

class TestParseSlackLink:
    def test_standard_url(self):
        url = "https://myworkspace.slack.com/archives/C09EFRG58SW/p1234567890123456"
        result = NotionClient.parse_slack_link(url)
        assert result == ("C09EFRG58SW", "1234567890.123456", "myworkspace")

    def test_no_workspace_url(self):
        url = "https://slack.com/archives/C09EFRG58SW/p1234567890123456"
        result = NotionClient.parse_slack_link(url)
        assert result == ("C09EFRG58SW", "1234567890.123456", None)

    def test_short_ts(self):
        url = "https://test.slack.com/archives/C12345/p1234567890"
        result = NotionClient.parse_slack_link(url)
        assert result == ("C12345", "1234567890", "test")

    def test_none_url(self):
        assert NotionClient.parse_slack_link(None) is None

    def test_empty_url(self):
        assert NotionClient.parse_slack_link("") is None

    def test_invalid_url(self):
        assert NotionClient.parse_slack_link("https://example.com") is None


class TestExtractReminderFields:
    def test_full_fields(self):
        page = {
            "id": "page-123",
            "properties": {
                "投稿リンク": {"url": "https://ws.slack.com/archives/C123/p111"},
                "警告送信日時": {"date": {"start": "2026-01-01T00:00:00Z"}},
                "投稿者": {"rich_text": [{"plain_text": "U_USER"}]},
                "投稿内容": {"title": [{"plain_text": "テスト投稿"}]},
                "該当条文": {"rich_text": [{"plain_text": "第3条"}]},
            },
        }
        fields = NotionClient.extract_reminder_fields(page)
        assert fields["page_id"] == "page-123"
        assert fields["post_link"] == "https://ws.slack.com/archives/C123/p111"
        assert fields["warning_sent_at"] == "2026-01-01T00:00:00Z"
        assert fields["poster"] == "U_USER"
        assert fields["title"] == "テスト投稿"
        assert fields["article_id"] == "第3条"

    def test_no_warning_sent_at_returns_none(self):
        page = {
            "id": "page-456",
            "last_edited_time": "2026-01-15T12:00:00.000Z",
            "properties": {
                "投稿リンク": {"url": None},
                "警告送信日時": {"date": None},
                "投稿者": {"rich_text": []},
                "投稿内容": {"title": []},
            },
        }
        fields = NotionClient.extract_reminder_fields(page)
        assert fields["warning_sent_at"] is None
        assert fields["poster"] is None
        assert fields["title"] == ""

    def test_explicit_warning_preferred(self):
        page = {
            "id": "p",
            "last_edited_time": "2026-02-01T00:00:00Z",
            "properties": {
                "警告送信日時": {"date": {"start": "2026-01-01T00:00:00Z"}},
                "投稿リンク": {},
                "投稿者": {"rich_text": []},
                "投稿内容": {"title": []},
            },
        }
        fields = NotionClient.extract_reminder_fields(page)
        assert fields["warning_sent_at"] == "2026-01-01T00:00:00Z"

    def test_missing_properties(self):
        page = {"id": "p", "properties": {}}
        fields = NotionClient.extract_reminder_fields(page)
        assert fields["page_id"] == "p"
        assert fields["post_link"] is None
        assert fields["warning_sent_at"] is None
        assert fields["article_id"] is None
        assert fields["template_page_id"] == ""

    def test_relation_template(self):
        page = {
            "id": "p",
            "properties": {
                "使用テンプレート": {"relation": [{"id": "tmpl-page-123"}]},
                "投稿リンク": {"url": None},
                "警告送信日時": {"date": None},
                "投稿者": {"rich_text": []},
                "投稿内容": {"title": []},
            },
        }
        fields = NotionClient.extract_reminder_fields(page)
        assert fields["template_page_id"] == "tmpl-page-123"

    def test_relation_empty(self):
        page = {
            "id": "p",
            "properties": {
                "使用テンプレート": {"relation": []},
                "投稿リンク": {"url": None},
                "警告送信日時": {"date": None},
                "投稿者": {"rich_text": []},
                "投稿内容": {"title": []},
            },
        }
        fields = NotionClient.extract_reminder_fields(page)
        assert fields["template_page_id"] == ""

    def test_article_relation(self):
        page = {
            "id": "p",
            "properties": {
                "対象条文": {"relation": [{"id": "article-page-1"}]},
                "投稿リンク": {"url": None},
                "警告送信日時": {"date": None},
                "投稿者": {"rich_text": []},
                "投稿内容": {"title": []},
            },
        }
        fields = NotionClient.extract_reminder_fields(page)
        assert fields["article_page_id"] == "article-page-1"

    def test_article_relation_empty(self):
        page = {
            "id": "p",
            "properties": {
                "対象条文": {"relation": []},
                "投稿リンク": {"url": None},
                "警告送信日時": {"date": None},
                "投稿者": {"rich_text": []},
                "投稿内容": {"title": []},
            },
        }
        fields = NotionClient.extract_reminder_fields(page)
        assert fields["article_page_id"] == ""


class TestIsPastThreshold:
    def test_past_threshold(self):
        past = (datetime.now(timezone.utc) - timedelta(hours=50)).isoformat()
        assert NotionClient.is_past_threshold(past, 48) is True

    def test_not_past_threshold(self):
        recent = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()
        assert NotionClient.is_past_threshold(recent, 48) is False

    def test_none_returns_false(self):
        assert NotionClient.is_past_threshold(None, 48) is False

    def test_invalid_string(self):
        assert NotionClient.is_past_threshold("not-a-date", 48) is False

    def test_z_suffix(self):
        past = (datetime.now(timezone.utc) - timedelta(hours=50)).strftime("%Y-%m-%dT%H:%M:%SZ")
        assert NotionClient.is_past_threshold(past, 48) is True

    def test_zero_hours(self):
        recent = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        assert NotionClient.is_past_threshold(recent, 0) is True


# ---- Reminder service functions ----

class TestCheckMessageExists:
    def test_message_exists(self):
        mock_slack = MagicMock()
        mock_slack.conversations_history.return_value = {
            "messages": [{"ts": "1234567890.123456"}]
        }
        assert check_message_exists(mock_slack, "C123", "1234567890.123456") is True

    def test_message_deleted(self):
        mock_slack = MagicMock()
        mock_slack.conversations_history.return_value = {"messages": []}
        assert check_message_exists(mock_slack, "C123", "1234567890.123456") is False

    def test_different_ts(self):
        mock_slack = MagicMock()
        mock_slack.conversations_history.return_value = {
            "messages": [{"ts": "9999999999.999999"}]
        }
        assert check_message_exists(mock_slack, "C123", "1234567890.123456") is False


class TestSendWarning:
    def test_success(self):
        mock_slack = MagicMock()
        text = "テスト警告文"
        assert send_warning(mock_slack, "C123", "1234567890.123456", text) is True
        mock_slack.chat_postMessage.assert_called_once_with(
            channel="C123",
            thread_ts="1234567890.123456",
            text=text,
        )

    def test_failure(self):
        from slack_sdk.errors import SlackApiError
        mock_slack = MagicMock()
        mock_slack.chat_postMessage.side_effect = SlackApiError(
            message="error", response=MagicMock(status_code=403)
        )
        assert send_warning(mock_slack, "C123", "1234567890.123456", "text") is False


class TestResolveSlackClient:
    def test_known_workspace(self):
        default = MagicMock()
        ws_client = MagicMock()
        result = _resolve_slack_client("myws", {"myws": ws_client}, default)
        assert result is ws_client

    def test_unknown_workspace(self):
        default = MagicMock()
        result = _resolve_slack_client("unknown", {"myws": MagicMock()}, default)
        assert result is default

    def test_none_workspace(self):
        default = MagicMock()
        result = _resolve_slack_client(None, {"myws": MagicMock()}, default)
        assert result is default

    def test_empty_clients(self):
        default = MagicMock()
        result = _resolve_slack_client("myws", {}, default)
        assert result is default


# ---- process_reminders integration ----

class TestProcessReminders:
    def _make_page(self, page_id="p1", link="https://ws.slack.com/archives/C123/p1234567890123456",
                   warning_sent_at="USE_DEFAULT", has_warning=True):
        if warning_sent_at == "USE_DEFAULT":
            warning_sent_at = (datetime.now(timezone.utc) - timedelta(hours=50)).isoformat()

        warning_date = {"date": {"start": warning_sent_at}} if has_warning else {"date": None}

        return {
            "id": page_id,
            "properties": {
                "投稿リンク": {"url": link},
                "警告送信日時": warning_date,
                "投稿者": {"rich_text": [{"plain_text": "U_TEST"}]},
                "投稿内容": {"title": [{"plain_text": "テスト投稿"}]},
            },
        }

    def _setup_notion_mock(self, pages):
        mock_notion = MagicMock(spec=NotionClient)
        mock_notion.query_approved_unreminded.return_value = pages
        mock_notion.extract_reminder_fields = NotionClient.extract_reminder_fields
        mock_notion.is_past_threshold = NotionClient.is_past_threshold
        mock_notion.parse_slack_link = NotionClient.parse_slack_link
        mock_notion.mark_48h_over.return_value = True
        mock_notion.mark_reminded.return_value = True
        mock_notion.update_status.return_value = True
        # get_page: デフォルトではページをそのまま返す（再取得でも同じ状態）
        mock_notion.get_page.side_effect = lambda pid: next(
            (p for p in pages if p["id"] == pid), None
        )
        return mock_notion

    def test_48h_over_status_update(self):
        """48h経過 → ステータスを48h_Overに更新、Slack送信なし"""
        mock_slack = MagicMock()
        mock_slack.conversations_history.return_value = {
            "messages": [{"ts": "1234567890.123456"}]
        }
        mock_notion = self._setup_notion_mock([self._make_page()])

        stats = process_reminders(slack=mock_slack, notion=mock_notion, hours_threshold=48)

        assert stats["expired"] == 1
        mock_notion.mark_48h_over.assert_called_once_with("p1")
        mock_slack.chat_postMessage.assert_not_called()

    def test_already_deleted(self):
        mock_slack = MagicMock()
        mock_slack.conversations_history.return_value = {"messages": []}
        mock_notion = self._setup_notion_mock([self._make_page()])

        stats = process_reminders(slack=mock_slack, notion=mock_notion, hours_threshold=48)

        assert stats["already_deleted"] == 1
        mock_slack.chat_postMessage.assert_not_called()
        mock_notion.mark_reminded.assert_called_once()

    def test_not_elapsed(self):
        recent = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()
        mock_slack = MagicMock()
        mock_slack.conversations_history.return_value = {
            "messages": [{"ts": "1234567890.123456"}]
        }
        mock_notion = self._setup_notion_mock([self._make_page(warning_sent_at=recent)])

        stats = process_reminders(slack=mock_slack, notion=mock_notion, hours_threshold=48)

        assert stats["skipped_not_elapsed"] == 1
        mock_slack.chat_postMessage.assert_not_called()

    def test_no_records(self):
        mock_slack = MagicMock()
        mock_notion = MagicMock(spec=NotionClient)
        mock_notion.query_approved_unreminded.return_value = []

        stats = process_reminders(slack=mock_slack, notion=mock_notion)

        assert stats["queried"] == 0
        mock_slack.chat_postMessage.assert_not_called()

    def test_workspace_routing(self):
        """ワークスペース別クライアントは存在確認に使用"""
        mock_default = MagicMock()
        mock_ws = MagicMock()
        mock_ws.conversations_history.return_value = {
            "messages": [{"ts": "1234567890.123456"}]
        }
        mock_notion = self._setup_notion_mock([self._make_page()])

        stats = process_reminders(
            slack=mock_default,
            notion=mock_notion,
            hours_threshold=48,
            slack_clients={"ws": mock_ws},
        )

        assert stats["expired"] == 1
        mock_ws.conversations_history.assert_called_once()
        mock_default.chat_postMessage.assert_not_called()
        mock_ws.chat_postMessage.assert_not_called()

    def test_dry_run_48h_over(self):
        mock_slack = MagicMock()
        mock_slack.conversations_history.return_value = {
            "messages": [{"ts": "1234567890.123456"}]
        }
        mock_notion = self._setup_notion_mock([self._make_page()])

        stats = process_reminders(
            slack=mock_slack, notion=mock_notion, hours_threshold=48, dry_run=True,
        )

        assert stats["expired"] == 1
        mock_slack.chat_postMessage.assert_not_called()
        mock_notion.mark_48h_over.assert_not_called()

    # ---- 初回警告テスト ----

    def test_initial_warning_sent_to_thread(self):
        """警告送信日時なし → 違反投稿スレッドに初回警告"""
        mock_slack = MagicMock()
        mock_slack.conversations_history.return_value = {
            "messages": [{"ts": "1234567890.123456"}]
        }
        mock_notion = self._setup_notion_mock([self._make_page(has_warning=False)])

        stats = process_reminders(slack=mock_slack, notion=mock_notion, hours_threshold=48)

        assert stats["warned"] == 1
        assert stats["expired"] == 0
        mock_slack.chat_postMessage.assert_called_once()
        call_args = mock_slack.chat_postMessage.call_args
        assert call_args.kwargs["channel"] == "C123"
        assert call_args.kwargs["thread_ts"] == "1234567890.123456"
        mock_notion.update_status.assert_called_once()
        assert mock_notion.update_status.call_args.args[0] == "p1"
        assert mock_notion.update_status.call_args.args[1] == "Approved"
        assert mock_notion.update_status.call_args.kwargs.get("warning_sent_at") is not None

    def test_initial_warning_uses_fallback_template(self):
        """初回警告はテンプレート（フォールバック）を使用"""
        mock_slack = MagicMock()
        mock_slack.conversations_history.return_value = {
            "messages": [{"ts": "1234567890.123456"}]
        }
        mock_notion = self._setup_notion_mock([self._make_page(has_warning=False)])

        stats = process_reminders(slack=mock_slack, notion=mock_notion, hours_threshold=48)

        assert stats["warned"] == 1
        call_args = mock_slack.chat_postMessage.call_args
        assert call_args.kwargs["text"] == FALLBACK_TEMPLATES["警告"]

    def test_initial_warning_dry_run(self):
        mock_slack = MagicMock()
        mock_slack.conversations_history.return_value = {
            "messages": [{"ts": "1234567890.123456"}]
        }
        mock_notion = self._setup_notion_mock([self._make_page(has_warning=False)])

        stats = process_reminders(
            slack=mock_slack, notion=mock_notion, hours_threshold=48, dry_run=True,
        )

        assert stats["warned"] == 1
        mock_slack.chat_postMessage.assert_not_called()
        mock_notion.update_status.assert_not_called()

    def test_initial_warning_message_deleted(self):
        mock_slack = MagicMock()
        mock_slack.conversations_history.return_value = {"messages": []}
        mock_notion = self._setup_notion_mock([self._make_page(has_warning=False)])

        stats = process_reminders(slack=mock_slack, notion=mock_notion, hours_threshold=48)

        assert stats["already_deleted"] == 1
        assert stats["warned"] == 0
        mock_slack.chat_postMessage.assert_not_called()
        mock_notion.mark_reminded.assert_called_once()

    def test_mixed_warning_and_48h_over(self):
        """初回警告対象と48h経過対象が混在"""
        mock_slack = MagicMock()
        mock_slack.conversations_history.return_value = {
            "messages": [{"ts": "1234567890.123456"}]
        }
        pages = [
            self._make_page(page_id="p1", has_warning=False),
            self._make_page(page_id="p2", has_warning=True),
        ]
        mock_notion = self._setup_notion_mock(pages)

        stats = process_reminders(slack=mock_slack, notion=mock_notion, hours_threshold=48)

        assert stats["warned"] == 1
        assert stats["expired"] == 1
        # 初回警告のみSlack送信、48h経過はステータス更新のみ
        mock_slack.chat_postMessage.assert_called_once()
        call_args = mock_slack.chat_postMessage.call_args
        assert call_args.kwargs["thread_ts"] == "1234567890.123456"

    def test_initial_warning_skipped_when_lambda_b_already_warned(self):
        """Lambda Bが先に警告済み → Lambda Cはスキップ（競合防止）"""
        mock_slack = MagicMock()
        mock_slack.conversations_history.return_value = {
            "messages": [{"ts": "1234567890.123456"}]
        }
        page = self._make_page(has_warning=False)
        mock_notion = self._setup_notion_mock([page])
        # get_page で再取得すると warning_sent_at がセットされている（Lambda Bが処理済み）
        fresh_page = self._make_page(has_warning=True)
        fresh_page["id"] = "p1"
        mock_notion.get_page.side_effect = lambda pid: fresh_page

        stats = process_reminders(slack=mock_slack, notion=mock_notion, hours_threshold=48)

        assert stats["warned"] == 0
        assert stats["skipped_not_elapsed"] == 1
        mock_slack.chat_postMessage.assert_not_called()

    def test_article_relation_resolves_name(self):
        mock_slack = MagicMock()
        mock_slack.conversations_history.return_value = {
            "messages": [{"ts": "1234567890.123456"}]
        }
        page = self._make_page(has_warning=False)
        page["properties"]["対象条文"] = {"relation": [{"id": "article-page-1"}]}
        page["properties"]["該当条文"] = {"rich_text": [{"plain_text": "11-iv"}]}
        mock_notion = self._setup_notion_mock([page])
        mock_notion.get_article_name.return_value = "AI Community参加規約 第11条(iv)"

        with patch("app_remind.services.reminder.resolve_template") as mock_resolve:
            mock_resolve.return_value = "{{article}}に違反"
            stats = process_reminders(slack=mock_slack, notion=mock_notion, hours_threshold=48)

        assert stats["warned"] == 1
        call_args = mock_slack.chat_postMessage.call_args
        assert "AI Community参加規約 第11条(iv)に違反" in call_args.kwargs["text"]

    def test_article_relation_fallback_to_rich_text(self):
        mock_slack = MagicMock()
        mock_slack.conversations_history.return_value = {
            "messages": [{"ts": "1234567890.123456"}]
        }
        page = self._make_page(has_warning=False)
        page["properties"]["対象条文"] = {"relation": []}
        page["properties"]["該当条文"] = {"rich_text": [{"plain_text": "11-iv"}]}
        mock_notion = self._setup_notion_mock([page])

        with patch("app_remind.services.reminder.resolve_template") as mock_resolve:
            mock_resolve.return_value = "{{article}}に違反"
            stats = process_reminders(slack=mock_slack, notion=mock_notion, hours_threshold=48)

        assert stats["warned"] == 1
        call_args = mock_slack.chat_postMessage.call_args
        assert "11-ivに違反" in call_args.kwargs["text"]
        mock_notion.get_article_name.assert_not_called()


# ---- Template manager: Relation対応 ----

class TestResolveTemplateWithRelation:
    @patch("common.template_manager.get_template_by_page_id")
    def test_relation_takes_priority(self, mock_get_by_id):
        mock_get_by_id.return_value = "Relationテンプレート本文"
        result = resolve_template("警告", api_key="key", db_id="db", template_page_id="tmpl-1")
        assert result == "Relationテンプレート本文"
        mock_get_by_id.assert_called_once_with("key", "tmpl-1")

    @patch("common.template_manager.get_template_by_page_id")
    @patch("common.template_manager.get_templates")
    def test_fallback_to_db_when_relation_fails(self, mock_get_templates, mock_get_by_id):
        mock_get_by_id.return_value = None
        mock_get_templates.return_value = {"警告": {"name": "標準", "body": "DB警告文"}}
        result = resolve_template("警告", api_key="key", db_id="db", template_page_id="bad-id")
        assert result == "DB警告文"

    def test_fallback_to_hardcoded_when_no_relation(self):
        result = resolve_template("警告", template_page_id="")
        assert result == FALLBACK_TEMPLATES["警告"]

    @patch("common.template_manager.get_template_by_page_id")
    def test_no_api_key_skips_relation(self, mock_get_by_id):
        result = resolve_template("警告", api_key="", template_page_id="tmpl-1")
        mock_get_by_id.assert_not_called()
        assert result == FALLBACK_TEMPLATES["警告"]


class TestFindArticlePageId:
    def test_found(self):
        notion = NotionClient(api_key="test", db_id="db", articles_db_id="articles-db")
        with patch("common.notion_client.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.ok = True
            mock_resp.json.return_value = {"results": [{"id": "article-page-1"}]}
            mock_post.return_value = mock_resp
            result = notion.find_article_page_id("11-iv")
            assert result == "article-page-1"

    def test_not_found(self):
        notion = NotionClient(api_key="test", db_id="db", articles_db_id="articles-db")
        with patch("common.notion_client.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.ok = True
            mock_resp.json.return_value = {"results": []}
            mock_post.return_value = mock_resp
            result = notion.find_article_page_id("nonexistent")
            assert result is None

    def test_no_articles_db_id(self):
        notion = NotionClient(api_key="test", db_id="db")
        result = notion.find_article_page_id("11-iv")
        assert result is None

    def test_empty_article_id(self):
        notion = NotionClient(api_key="test", db_id="db", articles_db_id="articles-db")
        result = notion.find_article_page_id("")
        assert result is None


class TestGetArticleName:
    @patch("common.notion_client.requests.get")
    def test_returns_article_number(self, mock_get):
        notion = NotionClient(api_key="test", db_id="db")
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {
            "properties": {
                "条文ID": {"title": [{"plain_text": "11-iv"}]},
                "条項番号": {"rich_text": [{"plain_text": "AI Community参加規約 第11条(iv)"}]},
            }
        }
        mock_get.return_value = mock_resp
        result = notion.get_article_name("article-page-1")
        assert result == "AI Community参加規約 第11条(iv)"

    @patch("common.notion_client.requests.get")
    def test_fallback_to_title(self, mock_get):
        notion = NotionClient(api_key="test", db_id="db")
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {
            "properties": {
                "条文ID": {"title": [{"plain_text": "11-iv"}]},
                "条項番号": {"rich_text": []},
            }
        }
        mock_get.return_value = mock_resp
        result = notion.get_article_name("article-page-1")
        assert result == "11-iv"

    @patch("common.notion_client.requests.get")
    def test_api_failure_returns_none(self, mock_get):
        notion = NotionClient(api_key="test", db_id="db")
        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 404
        mock_get.return_value = mock_resp
        result = notion.get_article_name("bad-page")
        assert result is None


class TestGetTemplateByPageId:
    @patch("common.template_manager.requests.get")
    def test_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {
            "properties": {
                "テンプレート名": {"title": [{"plain_text": "カスタム警告"}]},
                "用途": {"select": {"name": "警告"}},
                "本文": {"rich_text": [{"plain_text": "カスタム本文"}]},
            }
        }
        mock_get.return_value = mock_resp
        result = get_template_by_page_id("api-key", "page-123")
        assert result == "カスタム本文"

    @patch("common.template_manager.requests.get")
    def test_api_failure_returns_none(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 404
        mock_get.return_value = mock_resp
        result = get_template_by_page_id("api-key", "bad-page")
        assert result is None

    @patch("common.template_manager.requests.get")
    def test_empty_body_returns_none(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.json.return_value = {
            "properties": {
                "テンプレート名": {"title": [{"plain_text": "空テンプレ"}]},
                "用途": {"select": {"name": "警告"}},
                "本文": {"rich_text": []},
            }
        }
        mock_get.return_value = mock_resp
        result = get_template_by_page_id("api-key", "page-456")
        assert result is None


# ---- process_remind_requests ----

class TestProcessRemindRequests:
    def _make_page(self, page_id="p1", link="https://ws.slack.com/archives/C123/p1234567890123456"):
        return {
            "id": page_id,
            "properties": {
                "投稿リンク": {"url": link},
                "警告送信日時": {"date": {"start": (datetime.now(timezone.utc) - timedelta(hours=50)).isoformat()}},
                "投稿者": {"rich_text": [{"plain_text": "U_TEST"}]},
                "投稿内容": {"title": [{"plain_text": "テスト投稿"}]},
            },
        }

    def _setup_notion_mock(self, pages):
        mock_notion = MagicMock(spec=NotionClient)
        mock_notion.query_remind_requested.return_value = pages
        mock_notion.extract_reminder_fields = NotionClient.extract_reminder_fields
        mock_notion.parse_slack_link = NotionClient.parse_slack_link
        mock_notion.mark_reminded.return_value = True
        return mock_notion

    def test_remind_requested_sends_slack(self):
        """Remind_Requested → Slackにリマインド送信 + Reminded更新"""
        mock_slack = MagicMock()
        mock_slack.conversations_history.return_value = {
            "messages": [{"ts": "1234567890.123456"}]
        }
        mock_notion = self._setup_notion_mock([self._make_page()])

        stats = process_remind_requests(slack=mock_slack, notion=mock_notion)

        assert stats["reminded"] == 1
        mock_slack.chat_postMessage.assert_called_once()
        call_args = mock_slack.chat_postMessage.call_args
        assert call_args.kwargs["channel"] == "C123"
        assert call_args.kwargs["thread_ts"] == "1234567890.123456"
        mock_notion.mark_reminded.assert_called_once_with("p1")

    def test_remind_requested_message_deleted(self):
        """投稿が削除済み → Slack送信なし、Reminded更新"""
        mock_slack = MagicMock()
        mock_slack.conversations_history.return_value = {"messages": []}
        mock_notion = self._setup_notion_mock([self._make_page()])

        stats = process_remind_requests(slack=mock_slack, notion=mock_notion)

        assert stats["already_deleted"] == 1
        assert stats["reminded"] == 0
        mock_slack.chat_postMessage.assert_not_called()
        mock_notion.mark_reminded.assert_called_once()

    def test_remind_requested_no_records(self):
        mock_slack = MagicMock()
        mock_notion = MagicMock(spec=NotionClient)
        mock_notion.query_remind_requested.return_value = []

        stats = process_remind_requests(slack=mock_slack, notion=mock_notion)

        assert stats["queried"] == 0
        mock_slack.chat_postMessage.assert_not_called()

    def test_remind_requested_dry_run(self):
        mock_slack = MagicMock()
        mock_slack.conversations_history.return_value = {
            "messages": [{"ts": "1234567890.123456"}]
        }
        mock_notion = self._setup_notion_mock([self._make_page()])

        stats = process_remind_requests(slack=mock_slack, notion=mock_notion, dry_run=True)

        assert stats["reminded"] == 1
        mock_slack.chat_postMessage.assert_not_called()
        mock_notion.mark_reminded.assert_not_called()

    def test_remind_requested_no_link(self):
        mock_slack = MagicMock()
        page = self._make_page()
        page["properties"]["投稿リンク"] = {"url": None}
        mock_notion = self._setup_notion_mock([page])

        stats = process_remind_requests(slack=mock_slack, notion=mock_notion)

        assert stats["skipped_no_link"] == 1
        mock_slack.chat_postMessage.assert_not_called()
        mock_notion.mark_reminded.assert_called_once()
