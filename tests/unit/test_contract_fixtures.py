# tests/unit/test_contract_fixtures.py
import json
import pytest

jsonschema = pytest.importorskip("jsonschema")


def test_alert_button_value_fixture_matches_schema(load_contract_fixture, alert_button_value_schema):
    value = load_contract_fixture("alert_button_value.json")
    jsonschema.validate(instance=value, schema=alert_button_value_schema)


@pytest.mark.parametrize(
    "fixture_name, expected_action_id",
    [
        ("interactivity_button_click_approve.json", "approve_violation"),
        ("interactivity_button_click_dismiss.json", "dismiss_violation"),
    ],
)
def test_interactivity_fixture_has_expected_action_and_valid_value(
    load_contract_fixture,
    alert_button_value_schema,
    fixture_name,
    expected_action_id,
):
    payload = load_contract_fixture(fixture_name)

    assert payload["actions"][0]["action_id"] == expected_action_id

    value_str = payload["actions"][0]["value"]
    value = json.loads(value_str)
    jsonschema.validate(instance=value, schema=alert_button_value_schema)


def test_event_api_message_fixture_contains_must_fields(load_contract_fixture):
    apigw_event = load_contract_fixture("event_api_message.json")
    body = json.loads(apigw_event["body"])

    assert body["type"] == "event_callback"
    assert "team_id" in body
    assert "event_id" in body

    ev = body["event"]
    assert ev["type"] == "message"
    assert "channel" in ev
    assert "ts" in ev
    assert "text" in ev
