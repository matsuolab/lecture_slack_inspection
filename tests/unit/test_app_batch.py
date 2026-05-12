"""Lambda E (app_batch) ユニットテスト"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app_batch.services.scanner import iter_channel_messages
from app_batch.handler import _moderate


def _fake_aws_context():
    return SimpleNamespace(aws_request_id="req-test", function_name="aie09bot-batch-scan")


class TestIterChannelMessages:
    def test_filters_bot_and_subtype(self):
        client = MagicMock()
        client.conversations_history.return_value = {
            "messages": [
                {"ts": "1.0", "text": "hello", "user": "U1"},
                {"ts": "2.0", "text": "bot msg", "bot_id": "B1"},
                {"ts": "3.0", "text": "joined", "subtype": "channel_join"},
                {"ts": "4.0", "text": "world", "user": "U2"},
                {"ts": "5.0", "text": "", "user": "U3"},
            ],
            "has_more": False,
        }

        msgs = list(iter_channel_messages(client, "C1", sleep_ms=0))
        assert [m["ts"] for m in msgs] == ["1.0", "4.0"]

    def test_pagination(self):
        client = MagicMock()
        client.conversations_history.side_effect = [
            {
                "messages": [{"ts": "1.0", "text": "a", "user": "U1"}],
                "has_more": True,
                "response_metadata": {"next_cursor": "cursor1"},
            },
            {
                "messages": [{"ts": "2.0", "text": "b", "user": "U1"}],
                "has_more": False,
            },
        ]

        msgs = list(iter_channel_messages(client, "C1", sleep_ms=0))
        assert [m["ts"] for m in msgs] == ["1.0", "2.0"]
        assert client.conversations_history.call_count == 2

    def test_oldest_latest_passed(self):
        client = MagicMock()
        client.conversations_history.return_value = {"messages": [], "has_more": False}

        list(iter_channel_messages(client, "C1", oldest="100", latest="200", sleep_ms=0))
        kwargs = client.conversations_history.call_args.kwargs
        assert kwargs["oldest"] == "100"
        assert kwargs["latest"] == "200"


class TestHandlerDryRun:
    def test_dry_run_no_notion_write(self, monkeypatch):
        monkeypatch.setenv("USE_MOCK_OPENAI", "true")
        monkeypatch.setenv("BATCH_MAX_MESSAGES_PER_INVOKE", "10")
        monkeypatch.setenv("BATCH_SLEEP_MS", "0")

        with patch("app_batch.handler.load_config") as mock_load, \
             patch("app_batch.handler.WebClient") as mock_wc, \
             patch("app_batch.handler.NotionClient") as mock_nc, \
             patch("app_batch.handler.iter_channel_messages") as mock_iter:

            cfg = MagicMock()
            cfg.use_mock_openai = True
            cfg.max_messages_per_invoke = 10
            cfg.sleep_ms = 0
            cfg.slack_bot_token = "xoxb-test"
            cfg.openai_api_key = "sk-test"
            cfg.notion_api_key = "secret_notion"
            cfg.notion_db_id = "db_id"
            cfg.notion_articles_db_id = "articles_db_id"
            mock_load.return_value = cfg

            mock_iter.return_value = iter([
                {"ts": "1.0", "text": "違反 ワード", "user": "U1"},
                {"ts": "2.0", "text": "普通の投稿", "user": "U2"},
            ])

            from app_batch.handler import lambda_handler
            result = lambda_handler(
                {"team_id": "T1", "channel_id": "C1", "dry_run": True},
                _fake_aws_context(),
            )

            assert result["ok"] is True
            assert result["scanned"] == 2
            assert result["violations"] == 1
            assert result["dry_run"] is True
            mock_nc.assert_not_called()

    def test_missing_team_id(self):
        with patch("app_batch.handler.load_config"):
            from app_batch.handler import lambda_handler
            result = lambda_handler({"channel_id": "C1"}, _fake_aws_context())
            assert result["ok"] is False
            assert "team_id" in result["error"]


def test_moderate_passes_embedding_model_to_run_moderation(mocker):
    cfg = MagicMock()
    cfg.use_mock_openai = False
    cfg.openai_model = "gpt-5.1-mini"
    cfg.openai_embedding_model = "text-embedding-3-large"
    cfg.guidelines_text = "guideline"

    run_mock = mocker.patch("app_batch.handler.run_moderation")
    run_mock.return_value = MagicMock(
        is_violation=False,
        severity="low",
        categories=[],
        rationale="ok",
        recommended_reply="",
        confidence=0.1,
        article_id=None,
        method="LLM",
    )

    _moderate(cfg, MagicMock(), "sample")

    run_mock.assert_called_once_with(
        mocker.ANY,
        "gpt-5.1-mini",
        "guideline",
        "sample",
        "text-embedding-3-large",
    )
