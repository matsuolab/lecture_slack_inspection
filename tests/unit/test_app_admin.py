# tests/unit/test_app_admin.py

import json

import pytest

import app_admin.handler as admin


@pytest.fixture
def admin_common_mocks(monkeypatch):
    """app_admin.handler の外部依存を最小限モックする"""
    put_calls = []

    monkeypatch.setattr(admin, "build_context", lambda *args, **kwargs: {})
    monkeypatch.setattr(admin, "log_info", lambda *args, **kwargs: None)
    monkeypatch.setattr(admin, "log_error", lambda *args, **kwargs: None)

    monkeypatch.setattr(
        admin,
        "put_secure_parameter",
        lambda name, value, *args, **kwargs: put_calls.append((name, value)),
    )

    monkeypatch.setenv("OAUTH_ALLOWED_TEAM_IDS_PARAM_NAME", "/slack/oauth/allowed_team_ids")

    return {"put_calls": put_calls}


def _event(team_id: str) -> dict:
    return {"body": json.dumps({"team_id": team_id})}


def test_adds_new_team_id(monkeypatch, admin_common_mocks):
    """未登録のteam_idは許可リストへ追加され、addedが返ること"""
    monkeypatch.setattr(admin, "get_parameter_by_name_no_cache", lambda name: "T111,T222")

    resp = admin.lambda_handler(_event("T333AAAAA"), {})

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body == {"result": "added", "team_id": "T333AAAAA"}
    assert admin_common_mocks["put_calls"] == [
        ("/slack/oauth/allowed_team_ids", "T111,T222,T333AAAAA"),
    ]


def test_duplicate_team_id_is_skipped(monkeypatch, admin_common_mocks):
    """既に許可リストにあるteam_idはduplicateを返し、書き込みは行わないこと"""
    monkeypatch.setattr(admin, "get_parameter_by_name_no_cache", lambda name: "T111,T222AAAAA")

    resp = admin.lambda_handler(_event("T222AAAAA"), {})

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body == {"result": "duplicate", "team_id": "T222AAAAA"}
    assert admin_common_mocks["put_calls"] == []


@pytest.mark.parametrize("team_id", ["", "invalid", "t111aaaaa", "T111"])
def test_invalid_team_id_format_is_rejected(monkeypatch, admin_common_mocks, team_id):
    """形式不正なteam_idはinvalidを返し、SSMへは触れないこと"""
    called = {"count": 0}
    monkeypatch.setattr(
        admin,
        "get_parameter_by_name_no_cache",
        lambda name: called.__setitem__("count", called["count"] + 1) or "",
    )

    resp = admin.lambda_handler(_event(team_id), {})

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body == {"result": "invalid", "team_id": team_id}
    assert called["count"] == 0
    assert admin_common_mocks["put_calls"] == []


def test_missing_body_is_treated_as_invalid(admin_common_mocks):
    """bodyが無いリクエストはinvalidとして扱われること"""
    resp = admin.lambda_handler({}, {})

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body == {"result": "invalid", "team_id": ""}


def test_unexpected_error_returns_500(monkeypatch, admin_common_mocks):
    """SSM呼び出しで例外が発生した場合は500を返すこと"""
    def _raise(name):
        raise RuntimeError("ssm boom")

    monkeypatch.setattr(admin, "get_parameter_by_name_no_cache", _raise)

    resp = admin.lambda_handler(_event("T333AAAAA"), {})

    assert resp["statusCode"] == 500
    body = json.loads(resp["body"])
    assert body == {"result": "error"}
