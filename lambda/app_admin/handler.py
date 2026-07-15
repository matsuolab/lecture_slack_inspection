import json
import os
import re
from typing import Any

from common.secret_manager import get_parameter_by_name_no_cache, put_secure_parameter
from common.observability import build_context, log_info, log_error

SERVICE = "app_admin"

TEAM_ID_PATTERN = re.compile(r"^T[A-Z0-9]{8,}$")


def _response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _add_team_to_allowlist(team_id: str) -> str:
    """許可リストに team_id を追加する。戻り値は added / duplicate"""
    param_name = os.environ["OAUTH_ALLOWED_TEAM_IDS_PARAM_NAME"]
    current_value = get_parameter_by_name_no_cache(param_name)
    current_ids = {x.strip() for x in current_value.split(",") if x.strip()}

    if team_id in current_ids:
        return "duplicate"

    new_ids = sorted(current_ids | {team_id})
    put_secure_parameter(param_name, ",".join(new_ids))
    return "added"


def lambda_handler(event: dict[str, Any], context: Any) -> dict:
    ctx = build_context(event, context, service=SERVICE)
    log_info(ctx, action="admin_request_received")

    try:
        body = json.loads(event.get("body") or "{}")
        team_id = str(body.get("team_id") or "").strip()

        if not TEAM_ID_PATTERN.match(team_id):
            log_info(ctx, action="admin_invalid_team_id", team_id=team_id)
            return _response(200, {"result": "invalid", "team_id": team_id})

        result = _add_team_to_allowlist(team_id)
        log_info(ctx, action="admin_allowlist_updated", team_id=team_id, result=result)
        return _response(200, {"result": result, "team_id": team_id})

    except Exception as e:
        log_error(ctx, action="admin_request_failed", error=e)
        return _response(500, {"result": "error"})
