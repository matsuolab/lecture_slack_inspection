import json
from unittest.mock import ANY, MagicMock
import pytest
from app_inspect.handler import lambda_handler
from app_inspect.services.moderation import run_moderation
from app_inspect.services.violation_detector import ViolationDetector

jsonschema = pytest.importorskip("jsonschema")


def _find_first_button(blocks: list) -> dict:
    """
    Slack blocks から最初の button element を返す
    """
    for b in blocks:
        if b.get("type") == "actions":
            for el in b.get("elements", []):
                if el.get("type") == "button":
                    return el
    raise AssertionError("No button element found in blocks")


def test_lambdaA_emits_contract_compliant_button_value(
    load_contract_fixture, alert_button_value_schema, mock_external_services, mock_config, mocker
):
    # 署名OK（このテストでは署名そのものは目的ではない）
    mock_external_services["signature"].is_valid_request.return_value = True

    # moderation: 違反あり
    mock_result = MagicMock()
    mock_result.is_violation = True
    mock_result.severity = "high"
    mock_result.rationale = "spam"

    mock_result.confidence = 0.9
    mock_result.article_id = "A-123"
    mocker.patch("app_inspect.handler.run_moderation", return_value=mock_result)

    # Slack/Notionの戻り値
    mock_slack = mock_external_services["slack"]
    mock_slack.chat_getPermalink.return_value = {"permalink": "http://slack.com/p1"}
    mock_slack.chat_postMessage.return_value = {"ok": True, "ts": "1700000001.00001"}
    mock_notion = mock_external_services["notion"]
    mock_notion.create_violation_log.return_value = "page-id-123"
    mock_notion.find_article_page_id.return_value = "article-page-1"
    mock_notion.get_article_name.return_value = "テスト条文 第1条"

    # contracts/fixtures の Event API 入力をそのまま使う
    event = load_contract_fixture("event_api_message.json")
    # 実装がヘッダ参照する可能性に備えて最低限入れる（本番寄り）
    event.setdefault("headers", {})
    event["headers"].update({"x-slack-signature": "dummy", "x-slack-request-timestamp": "123"})

    resp = lambda_handler(event, {})
    assert resp["statusCode"] == 200

    # Slackへ private通知したか
    mock_slack.chat_postMessage.assert_called_once()
    _, kwargs = mock_slack.chat_postMessage.call_args
    blocks = kwargs["blocks"]

    mock_notion.create_violation_log.assert_called_once()
    call_kwargs = mock_notion.create_violation_log.call_args.kwargs
    
    assert call_kwargs["confidence"] == 0.9
    assert call_kwargs["article_id"] == "A-123"
    assert call_kwargs["post_content"] is not None
    assert call_kwargs.get("message_ts") is not None

    # ボタン契約を検証
    btn = _find_first_button(blocks)
    assert btn["action_id"] == "approve_violation"

    value = json.loads(btn["value"])
    jsonschema.validate(instance=value, schema=alert_button_value_schema)

    # 入力イベントと整合することを検証（ここでA-B間の壊れを止める）
    body = json.loads(event["body"])
    ev = body["event"]

    assert value["origin_channel"] == ev["channel"]
    assert value["origin_ts"] == ev["ts"]
    assert value["trace_id"] == f"slack:{body['event_id']}"
    assert value.get("article_id") == "テスト条文 第1条"


def test_run_moderation_passes_models_to_detector(mocker):
    client = MagicMock()
    detector_instance = MagicMock()
    detector_instance.detect.return_value = MagicMock(
        is_violation=False,
        confidence=0.2,
        category=None,
        reason="ok",
        article_id=None,
        method="LLM",
    )
    detector_cls = mocker.patch("app_inspect.services.moderation.ViolationDetector", return_value=detector_instance)

    result = run_moderation(
        client=client,
        model="gpt-4o-mini",
        guidelines="guideline",
        message_text="hello",
        embedding_model="text-embedding-3-small",
    )

    detector_cls.assert_called_once_with(
        openai_client=ANY,
        judge_model="gpt-4o-mini",
        embedding_model="text-embedding-3-small",
    )
    detector_instance.detect.assert_called_once_with("hello")
    assert result.is_violation is False


def test_violation_detector_uses_configured_models():
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=json.dumps({"violation_score": 0, "confidence": 0.1, "reason": "ok"})))]
    )
    client.embeddings.create.return_value = MagicMock(
        data=[MagicMock(embedding=[0.1, 0.2, 0.3])]
    )

    detector = ViolationDetector(
        openai_client=client,
        judge_model="gpt-4o-mini",
        embedding_model="text-embedding-3-small",
    )

    detector._judge_by_llm("sample text", [{"id": "A-001", "article": "A-001", "content": "rule"}])
    detector._get_embedding("sample text")

    chat_kwargs = client.chat.completions.create.call_args.kwargs
    embed_kwargs = client.embeddings.create.call_args.kwargs
    assert chat_kwargs["model"] == "gpt-4o-mini"
    assert embed_kwargs["model"] == "text-embedding-3-small"

def _build_message_changed_event(
    *,
    base_event: dict,
    before_text: str,
    after_text: str,
    message_ts: str = "1700000000.000100",
    user_id: str = "U123",
    channel_id: str = "C123",
) -> dict:
    event = dict(base_event)
    body = json.loads(event["body"])

    body["event"] = {
        "type": "message",
        "subtype": "message_changed",
        "hidden": True,
        "channel": channel_id,
        "message": {
            "type": "message",
            "user": user_id,
            "text": after_text,
            "ts": message_ts,
            "edited": {
                "user": user_id,
                "ts": "1700000010.000200",
            },
        },
        "previous_message": {
            "type": "message",
            "user": user_id,
            "text": before_text,
            "ts": message_ts,
        },
        "event_ts": "1700000010.000200",
    }

    event["body"] = json.dumps(body)
    event.setdefault("headers", {})
    event["headers"].update(
        {
            "x-slack-signature": "dummy",
            "x-slack-request-timestamp": "123",
        }
    )
    return event


def test_edit_non_violation_to_violation_creates_new_alert(
    load_contract_fixture,
    mock_external_services,
    mock_config,
    mocker,
):
    mock_external_services["signature"].is_valid_request.return_value = True

    mock_result = MagicMock()
    mock_result.is_violation = True
    mock_result.severity = "high"
    mock_result.rationale = "edited spam"
    mock_result.confidence = 0.95
    mock_result.article_id = "A-123"
    mock_result.categories = ["spam"]
    mock_result.method = "LLM"
    mocker.patch("app_inspect.handler.run_moderation", return_value=mock_result)

    mock_slack = mock_external_services["slack"]
    mock_slack.chat_getPermalink.return_value = {"permalink": "http://slack.com/p1"}
    mock_slack.chat_postMessage.return_value = {"ok": True, "ts": "1700000001.00001"}

    mock_notion = mock_external_services["notion"]
    mock_notion.find_violation_by_message_ts.return_value = None
    mock_notion.create_violation_log.return_value = "page-id-123"
    mock_notion.find_article_page_id.return_value = "article-page-1"
    mock_notion.get_article_name.return_value = "テスト条文 第1条"

    event = _build_message_changed_event(
        base_event=load_contract_fixture("event_api_message.json"),
        before_text="これはセーフです",
        after_text="これは違反です",
    )

    resp = lambda_handler(event, {})
    assert resp["statusCode"] == 200

    mock_notion.create_violation_log.assert_called_once()
    mock_slack.chat_postMessage.assert_called_once()

    _, kwargs = mock_slack.chat_postMessage.call_args
    assert kwargs["channel"] == mock_config.alert_private_channel_id
    assert "thread_ts" not in kwargs


def test_edit_existing_violation_to_still_violation_posts_thread_notice(
    load_contract_fixture,
    mock_external_services,
    mock_config,
    mocker,
):
    mock_external_services["signature"].is_valid_request.return_value = True

    mock_result = MagicMock()
    mock_result.is_violation = True
    mock_result.severity = "high"
    mock_result.rationale = "still violation"
    mock_result.confidence = 0.88
    mock_result.article_id = "A-123"
    mock_result.categories = ["spam"]
    mock_result.method = "LLM"
    mocker.patch("app_inspect.handler.run_moderation", return_value=mock_result)

    mock_slack = mock_external_services["slack"]
    mock_notion = mock_external_services["notion"]
    mock_notion.find_violation_by_message_ts.return_value = {
        "id": "page-id-123",
        "properties": {
            "対応ステータス": {"select": {"name": "警告済み"}},
            "通知チャンネルID": {"rich_text": [{"plain_text": "C_ADMIN"}]},
            "通知メッセージTS": {"rich_text": [{"plain_text": "1700009999.000100"}]},
            "投稿リンク": {"url": "http://slack.com/p1"},
        },
    }

    event = _build_message_changed_event(
        base_event=load_contract_fixture("event_api_message.json"),
        before_text="前の違反文",
        after_text="編集後も違反文",
    )

    resp = lambda_handler(event, {})
    assert resp["statusCode"] == 200

    mock_notion.create_violation_log.assert_not_called()
    mock_slack.chat_postMessage.assert_called_once()

    _, kwargs = mock_slack.chat_postMessage.call_args
    assert kwargs["channel"] == "C_ADMIN"
    assert kwargs["thread_ts"] == "1700009999.000100"
    assert "投稿が編集されました" in kwargs["text"]


def test_edit_existing_violation_to_clean_closes_violation(
    load_contract_fixture,
    mock_external_services,
    mock_config,
    mocker,
):
    mock_external_services["signature"].is_valid_request.return_value = True

    mock_result = MagicMock()
    mock_result.is_violation = False
    mock_result.severity = "low"
    mock_result.rationale = "clean after edit"
    mock_result.confidence = 0.20
    mock_result.article_id = None
    mock_result.categories = []
    mock_result.method = "LLM"
    mocker.patch("app_inspect.handler.run_moderation", return_value=mock_result)

    mock_slack = mock_external_services["slack"]
    mock_slack.users_info.return_value = {
        "user": {
            "profile": {
                "display_name": "user123",
                "real_name": "user123",
            }
        }
    }
    mock_slack.conversations_info.return_value = {"channel": {"name": "general"}}
    mock_slack.team_info.return_value = {"team": {"name": "workspace"}}

    mock_notion = mock_external_services["notion"]
    mock_notion.find_violation_by_message_ts.return_value = {
        "id": "page-id-123",
        "properties": {
            "対応ステータス": {"select": {"name": "警告済み"}},
            "通知チャンネルID": {"rich_text": [{"plain_text": "C_ADMIN"}]},
            "通知メッセージTS": {"rich_text": [{"plain_text": "1700009999.000100"}]},
            "投稿リンク": {"url": "http://slack.com/p1"},
        },
    }

    event = _build_message_changed_event(
        base_event=load_contract_fixture("event_api_message.json"),
        before_text="違反です",
        after_text="~~違反~~",
    )

    resp = lambda_handler(event, {})
    assert resp["statusCode"] == 200

    mock_notion.mark_closed_by_edit.assert_called_once()
    mock_notion.create_violation_log.assert_not_called()

    _, kwargs = mock_slack.chat_postMessage.call_args
    assert kwargs["channel"] == "C_ADMIN"
    assert kwargs["thread_ts"] == "1700009999.000100"
    assert "対応終了" in kwargs["text"]