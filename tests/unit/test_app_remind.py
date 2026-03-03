"""Lambda C (app_remind) ユニットテスト"""

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

from common.notion_client import NotionClient
from app_remind.services.reminder import (
    check_message_exists,
    send_warning,
    send_reminder,
    process_reminders,
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
        """警告送信日時が空の場合、フォールバックなしでNoneを返す"""
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
        """使用テンプレートRelationからpage_idを抽出"""
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
        """使用テンプレートRelationが未設定の場合は空文字"""
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
        """対象条文Relationからpage_idを抽出"""
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
        """対象条文Relationが未設定の場合は空文字"""
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


# ---- Workspace property in create_violation_log ----

class TestWorkspaceProperty:
    def test_workspace_set_from_post_link(self):
        """create_violation_log がワークスペースプロパティを設定すること"""
        notion = NotionClient(api_key="test", db_id="db")
        with patch("common.notion_client.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.ok = True
            mock_resp.json.return_value = {"id": "page-1"}
            mock_post.return_value = mock_resp

            notion.create_violation_log(
                post_content="test",
                user_id="U1",
                channel="C1",
                result="Violation",
                method="OpenAI",
                post_link="https://myws.slack.com/archives/C123/p111",
            )

            call_args = mock_post.call_args
            props = call_args.kwargs["json"]["properties"]
            assert props["ワークスペース"] == {"select": {"name": "myws"}}

    def test_article_relation_set_from_article_id(self):
        """article_idが指定されたとき、対象条文Relationが設定されること"""
        notion = NotionClient(api_key="test", db_id="db", articles_db_id="articles-db")
        with patch("common.notion_client.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.ok = True
            mock_resp.json.return_value = {"id": "page-1"}
            mock_post.return_value = mock_resp

            with patch.object(notion, "find_article_page_id", return_value="article-page-123"):
                notion.create_violation_log(
                    post_content="test",
                    user_id="U1",
                    channel="C1",
                    result="Violation",
                    method="OpenAI",
                    article_id="11-iv",
                )

            call_args = mock_post.call_args
            props = call_args.kwargs["json"]["properties"]
            assert props["該当条文"] == {"rich_text": [{"text": {"content": "11-iv"}}]}
            assert props["対象条文"] == {"relation": [{"id": "article-page-123"}]}

    def test_article_relation_not_set_when_not_found(self):
        """articles_dbにarticle_idが見つからない場合はRelation設定されないこと"""
        notion = NotionClient(api_key="test", db_id="db", articles_db_id="articles-db")
        with patch("common.notion_client.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.ok = True
            mock_resp.json.return_value = {"id": "page-1"}
            mock_post.return_value = mock_resp

            with patch.object(notion, "find_article_page_id", return_value=None):
                notion.create_violation_log(
                    post_content="test",
                    user_id="U1",
                    channel="C1",
                    result="Violation",
                    method="OpenAI",
                    article_id="unknown-id",
                )

            call_args = mock_post.call_args
            props = call_args.kwargs["json"]["properties"]
            assert props["該当条文"] == {"rich_text": [{"text": {"content": "unknown-id"}}]}
            assert "対象条文" not in props

    def test_no_workspace_for_slack_com(self):
        """slack.com URLの場合はワークスペースプロパティが設定されないこと"""
        notion = NotionClient(api_key="test", db_id="db")
        with patch("common.notion_client.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.ok = True
            mock_resp.json.return_value = {"id": "page-1"}
            mock_post.return_value = mock_resp

            notion.create_violation_log(
                post_content="test",
                user_id="U1",
                channel="C1",
                result="Violation",
                method="OpenAI",
                post_link="https://slack.com/archives/C123/p111",
            )

            call_args = mock_post.call_args
            props = call_args.kwargs["json"]["properties"]
            assert "ワークスペース" not in props


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


class TestSendReminder:
    def test_success(self):
        mock_slack = MagicMock()
        text = "テストリマインド文"
        assert send_reminder(mock_slack, "C123", "1234567890.123456", text) is True
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
        assert send_reminder(mock_slack, "C123", "1234567890.123456", "text") is False


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
        """テスト用ページを作成。has_warning=Falseで警告送信日時なし（初回警告対象）"""
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
        mock_notion.mark_reminded.return_value = True
        mock_notion.update_status.return_value = True
        return mock_notion

    def test_send_reminder(self):
        mock_slack = MagicMock()
        mock_slack.conversations_history.return_value = {
            "messages": [{"ts": "1234567890.123456"}]
        }
        mock_notion = self._setup_notion_mock([self._make_page()])

        stats = process_reminders(slack=mock_slack, notion=mock_notion, hours_threshold=48)

        assert stats["reminded"] == 1
        mock_slack.chat_postMessage.assert_called_once()
        mock_notion.mark_reminded.assert_called_once_with("p1")

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

        assert stats["reminded"] == 1
        mock_ws.chat_postMessage.assert_called_once()
        mock_default.chat_postMessage.assert_not_called()

    def test_dry_run_reminder(self):
        mock_slack = MagicMock()
        mock_slack.conversations_history.return_value = {
            "messages": [{"ts": "1234567890.123456"}]
        }
        mock_notion = self._setup_notion_mock([self._make_page()])

        stats = process_reminders(
            slack=mock_slack, notion=mock_notion, hours_threshold=48, dry_run=True
        )

        assert stats["reminded"] == 1
        mock_slack.chat_postMessage.assert_not_called()
        mock_notion.mark_reminded.assert_not_called()

    # ---- 初回警告テスト ----

    def test_initial_warning_sent(self):
        """警告送信日時なし → 初回警告を送信し、warning_sent_atを記録"""
        mock_slack = MagicMock()
        mock_slack.conversations_history.return_value = {
            "messages": [{"ts": "1234567890.123456"}]
        }
        mock_notion = self._setup_notion_mock([self._make_page(has_warning=False)])

        stats = process_reminders(slack=mock_slack, notion=mock_notion, hours_threshold=48)

        assert stats["warned"] == 1
        assert stats["reminded"] == 0
        mock_slack.chat_postMessage.assert_called_once_with(
            channel="C123",
            thread_ts="1234567890.123456",
            text=FALLBACK_TEMPLATES["警告"],
        )
        mock_notion.update_status.assert_called_once()
        call_args = mock_notion.update_status.call_args
        assert call_args.args[0] == "p1"
        assert call_args.args[1] == "Approved"
        assert call_args.kwargs.get("warning_sent_at") is not None

    def test_initial_warning_dry_run(self):
        """dry_run=Trueの場合、初回警告は送信しない"""
        mock_slack = MagicMock()
        mock_slack.conversations_history.return_value = {
            "messages": [{"ts": "1234567890.123456"}]
        }
        mock_notion = self._setup_notion_mock([self._make_page(has_warning=False)])

        stats = process_reminders(
            slack=mock_slack, notion=mock_notion, hours_threshold=48, dry_run=True
        )

        assert stats["warned"] == 1
        mock_slack.chat_postMessage.assert_not_called()
        mock_notion.update_status.assert_not_called()

    def test_initial_warning_message_deleted(self):
        """警告送信日時なし + 投稿削除済み → 警告送信せず、mark_reminded"""
        mock_slack = MagicMock()
        mock_slack.conversations_history.return_value = {"messages": []}
        mock_notion = self._setup_notion_mock([self._make_page(has_warning=False)])

        stats = process_reminders(slack=mock_slack, notion=mock_notion, hours_threshold=48)

        assert stats["already_deleted"] == 1
        assert stats["warned"] == 0
        mock_slack.chat_postMessage.assert_not_called()
        mock_notion.mark_reminded.assert_called_once()

    def test_mixed_warning_and_reminder(self):
        """初回警告対象とリマインド対象が混在するケース"""
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
        assert stats["reminded"] == 1
        assert mock_slack.chat_postMessage.call_count == 2

    def test_article_relation_resolves_name(self):
        """対象条文Relationから条文名が解決され、テンプレートに使用されること"""
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
        # テンプレートに条文名が使用されていること
        call_args = mock_slack.chat_postMessage.call_args
        assert "AI Community参加規約 第11条(iv)に違反" in call_args.kwargs["text"]

    def test_article_relation_fallback_to_rich_text(self):
        """対象条文Relationが空の場合は該当条文(rich_text)にフォールバック"""
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

    def test_relation_template_used(self):
        """Relation指定のテンプレートが使用されること"""
        mock_slack = MagicMock()
        mock_slack.conversations_history.return_value = {
            "messages": [{"ts": "1234567890.123456"}]
        }
        page = self._make_page(has_warning=False)
        page["properties"]["使用テンプレート"] = {"relation": [{"id": "tmpl-page-1"}]}
        mock_notion = self._setup_notion_mock([page])

        custom_body = "カスタムテンプレートの本文です"
        with patch("app_remind.services.reminder.resolve_template", return_value=custom_body):
            stats = process_reminders(slack=mock_slack, notion=mock_notion, hours_threshold=48)

        assert stats["warned"] == 1
        call_args = mock_slack.chat_postMessage.call_args
        assert custom_body in call_args.kwargs["text"]


# ---- Template manager: Relation対応 ----

class TestResolveTemplateWithRelation:
    @patch("common.template_manager.get_template_by_page_id")
    def test_relation_takes_priority(self, mock_get_by_id):
        """template_page_id指定時はRelationテンプレートが優先される"""
        mock_get_by_id.return_value = "Relationテンプレート本文"
        result = resolve_template("警告", api_key="key", db_id="db", template_page_id="tmpl-1")
        assert result == "Relationテンプレート本文"
        mock_get_by_id.assert_called_once_with("key", "tmpl-1")

    @patch("common.template_manager.get_template_by_page_id")
    @patch("common.template_manager.get_templates")
    def test_fallback_to_db_when_relation_fails(self, mock_get_templates, mock_get_by_id):
        """Relation取得失敗時はテンプレートDBにフォールバック"""
        mock_get_by_id.return_value = None
        mock_get_templates.return_value = {"警告": {"name": "標準", "body": "DB警告文"}}
        result = resolve_template("警告", api_key="key", db_id="db", template_page_id="bad-id")
        assert result == "DB警告文"

    def test_fallback_to_hardcoded_when_no_relation(self):
        """template_page_id空ならフォールバック"""
        result = resolve_template("警告", template_page_id="")
        assert result == FALLBACK_TEMPLATES["警告"]

    @patch("common.template_manager.get_template_by_page_id")
    def test_no_api_key_skips_relation(self, mock_get_by_id):
        """api_keyなしの場合はRelation検索をスキップ"""
        result = resolve_template("警告", api_key="", template_page_id="tmpl-1")
        mock_get_by_id.assert_not_called()
        assert result == FALLBACK_TEMPLATES["警告"]


class TestFindArticlePageId:
    def test_found(self):
        """条文IDからpage_idを取得"""
        notion = NotionClient(api_key="test", db_id="db", articles_db_id="articles-db")
        with patch("common.notion_client.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.ok = True
            mock_resp.json.return_value = {
                "results": [{"id": "article-page-1"}]
            }
            mock_post.return_value = mock_resp

            result = notion.find_article_page_id("11-iv")
            assert result == "article-page-1"

    def test_not_found(self):
        """条文IDが見つからない場合はNone"""
        notion = NotionClient(api_key="test", db_id="db", articles_db_id="articles-db")
        with patch("common.notion_client.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.ok = True
            mock_resp.json.return_value = {"results": []}
            mock_post.return_value = mock_resp

            result = notion.find_article_page_id("nonexistent")
            assert result is None

    def test_no_articles_db_id(self):
        """articles_db_id未設定の場合はNone"""
        notion = NotionClient(api_key="test", db_id="db")
        result = notion.find_article_page_id("11-iv")
        assert result is None

    def test_empty_article_id(self):
        """空のarticle_idの場合はNone"""
        notion = NotionClient(api_key="test", db_id="db", articles_db_id="articles-db")
        result = notion.find_article_page_id("")
        assert result is None


class TestGetArticleName:
    @patch("common.notion_client.requests.get")
    def test_returns_article_number(self, mock_get):
        """条項番号から条文名を取得"""
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
        """条項番号が空の場合はタイトル（条文ID）にフォールバック"""
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
        """API失敗時はNone"""
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
        """ページIDからテンプレート本文を取得"""
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
        """API失敗時はNoneを返す"""
        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 404
        mock_get.return_value = mock_resp

        result = get_template_by_page_id("api-key", "bad-page")
        assert result is None

    @patch("common.template_manager.requests.get")
    def test_empty_body_returns_none(self, mock_get):
        """本文が空の場合はNoneを返す"""
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
