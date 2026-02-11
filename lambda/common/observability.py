from __future__ import annotations

import os
import json
import time
import uuid
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs

STAGE = os.getenv("STAGE", "dev")  # dev/stg/prod
METRICS_NAMESPACE = os.getenv("METRICS_NAMESPACE", "SlackModerationBot")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

_LEVEL_ORDER = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _hash16(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]

def _truncate_str(s: str, limit: int = 500) -> str:
    return s if len(s) <= limit else s[:limit] + "...(truncated)"

def _safe_jsonable(v: Any) -> Any:
    try:
        if isinstance(v, str):
            return _truncate_str(v)
        if isinstance(v, (int, float, bool)) or v is None:
            return v
        if isinstance(v, dict):
            out = {}
            for k, vv in list(v.items())[:50]:
                out[str(k)] = _safe_jsonable(vv)
            if len(v) > 50:
                out["_truncated_keys"] = len(v) - 50
            return out
        if isinstance(v, list):
            out = [_safe_jsonable(x) for x in v[:50]]
            if len(v) > 50:
                out.append({"_truncated_items": len(v) - 50})
            return out
        return _truncate_str(str(v))
    except Exception:
        return "<unserializable>"

def _should_log(level: str) -> bool:
    return _LEVEL_ORDER.get(level, 20) >= _LEVEL_ORDER.get(LOG_LEVEL, 20)

def _json_print(obj: Dict[str, Any]) -> None:
    print(json.dumps(obj, ensure_ascii=False, separators=(",", ":")))

def _lower_headers(headers: Optional[Dict[str, Any]]) -> Dict[str, str]:
    if not headers:
        return {}
    return {str(k).lower(): str(v) for k, v in headers.items() if k is not None}


# ---- Slack event parsing (best-effort) ----
def parse_apigw_body(event: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    Returns (event_api_json, interactivity_payload_json).
    Supports:
      - API Gateway proxy event: headers + body
      - direct Slack JSON (local tests)
    """
    # Already Slack JSON (local tests etc.)
    if "body" not in event and ("event_id" in event or event.get("type") in ("event_callback", "url_verification")):
        return event, None

    body = event.get("body")
    if body is None:
        return None, None

    if isinstance(body, dict):
        if "event_id" in body or body.get("type") in ("event_callback", "url_verification"):
            return body, None
        if "actions" in body and "team" in body:
            return None, body
        return None, None

    body_str = str(body)
    headers = _lower_headers(event.get("headers"))
    content_type = headers.get("content-type", "")

    # Slack Event API: application/json
    if "application/json" in content_type:
        try:
            j = json.loads(body_str)
            if "event_id" in j or j.get("type") in ("event_callback", "url_verification"):
                return j, None
        except Exception:
            return None, None

    # Slack Interactivity: x-www-form-urlencoded payload=JSON
    if "application/x-www-form-urlencoded" in content_type or "payload=" in body_str:
        try:
            qs = parse_qs(body_str)
            payload_list = qs.get("payload")
            if payload_list:
                payload = json.loads(payload_list[0])
                return None, payload
        except Exception:
            return None, None

    # Fallback: try JSON
    try:
        j = json.loads(body_str)
        if "event_id" in j or j.get("type") in ("event_callback", "url_verification"):
            return j, None
        if "actions" in j and "team" in j:
            return None, j
    except Exception:
        pass

    return None, None

def extract_from_event_api(body: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    event_id = body.get("event_id")
    team_id = body.get("team_id")
    inner = body.get("event") or {}
    channel_id = inner.get("channel") if isinstance(inner, dict) else None
    message_ts = None
    if isinstance(inner, dict):
        message_ts = inner.get("ts") or inner.get("event_ts")
    return event_id, team_id, channel_id, message_ts

def _try_extract_trace_from_button_value(payload: Dict[str, Any]) -> Optional[str]:
    try:
        actions = payload.get("actions") or []
        if not actions:
            return None
        v = actions[0].get("value")
        if not v:
            return None
        if isinstance(v, str):
            vv = json.loads(v)
            if isinstance(vv, dict) and vv.get("trace_id"):
                return str(vv["trace_id"])
        if isinstance(v, dict) and v.get("trace_id"):
            return str(v["trace_id"])
    except Exception:
        return None
    return None

def extract_from_interactivity(payload: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str], Optional[str]]:
    trace_id = _try_extract_trace_from_button_value(payload)

    team_id = (payload.get("team") or {}).get("id")
    channel_id = (payload.get("channel") or {}).get("id")

    message_ts = None
    msg = payload.get("message") or {}
    container = payload.get("container") or {}
    if isinstance(msg, dict):
        message_ts = msg.get("ts") or message_ts
    if isinstance(container, dict):
        message_ts = container.get("message_ts") or message_ts

    action_id = None
    actions = payload.get("actions") or []
    if actions and isinstance(actions[0], dict):
        action_id = actions[0].get("action_id")

    return trace_id, team_id, channel_id, message_ts, action_id


# ---- Context ----
@dataclass(frozen=True)
class ObsContext:
    service: str
    env: str
    trace_id: str
    request_id: Optional[str]
    function_name: Optional[str]

    slack_event_id: Optional[str] = None
    slack_team_id: Optional[str] = None
    slack_channel_id: Optional[str] = None
    slack_message_ts: Optional[str] = None
    slack_action_id: Optional[str] = None


def build_context(event: Dict[str, Any], aws_context: Any, service: str) -> ObsContext:
    event_api_body, inter_payload = parse_apigw_body(event)

    slack_event_id = None
    slack_team_id = None
    slack_channel_id = None
    slack_message_ts = None
    slack_action_id = None
    trace_from_payload = None

    if event_api_body:
        slack_event_id, slack_team_id, slack_channel_id, slack_message_ts = extract_from_event_api(event_api_body)

    if inter_payload:
        trace_from_payload, team2, ch2, ts2, slack_action_id = extract_from_interactivity(inter_payload)
        slack_team_id = slack_team_id or team2
        slack_channel_id = slack_channel_id or ch2
        slack_message_ts = slack_message_ts or ts2

    if trace_from_payload:
        trace_id = trace_from_payload
    elif slack_event_id:
        trace_id = f"slack:{slack_event_id}"
    elif slack_team_id or slack_channel_id or slack_message_ts:
        base = f"{slack_team_id or '-'}|{slack_channel_id or '-'}|{slack_message_ts or '-'}"
        trace_id = f"msg:{_hash16(base)}"
    else:
        trace_id = f"gen:{uuid.uuid4().hex[:16]}"

    return ObsContext(
        service=service,
        env=STAGE,
        trace_id=trace_id,
        request_id=getattr(aws_context, "aws_request_id", None),
        function_name=getattr(aws_context, "function_name", None),
        slack_event_id=slack_event_id,
        slack_team_id=slack_team_id,
        slack_channel_id=slack_channel_id,
        slack_message_ts=slack_message_ts,
        slack_action_id=slack_action_id,
    )


# ---- Logging ----
def log(level: str, ctx: ObsContext, action: str, result: str, **fields: Any) -> None:
    if not _should_log(level):
        return

    payload: Dict[str, Any] = {
        "ts": _now_iso(),
        "level": level,
        "service": ctx.service,
        "env": ctx.env,
        "trace_id": ctx.trace_id,
        "action": action,
        "result": result,
        "request_id": ctx.request_id,
        "function_name": ctx.function_name,
        "slack_event_id": ctx.slack_event_id,
        "slack_team_id": ctx.slack_team_id,
        "slack_channel_id": ctx.slack_channel_id,
        "slack_message_ts": ctx.slack_message_ts,
        "slack_action_id": ctx.slack_action_id,
    }

    for k, v in fields.items():
        payload[k] = _safe_jsonable(v)

    _json_print(payload)

def log_info(ctx: ObsContext, action: str, result: str = "success", **fields: Any) -> None:
    log("INFO", ctx, action, result, **fields)

def log_warn(ctx: ObsContext, action: str, result: str = "fail", **fields: Any) -> None:
    log("WARN", ctx, action, result, **fields)

def log_error(ctx: ObsContext, action: str, error: Exception, result: str = "fail", **fields: Any) -> None:
    log("ERROR", ctx, action, result, error_type=type(error).__name__, error_message=_truncate_str(str(error), 800), **fields)


# ---- Metrics (CloudWatch EMF) ----
def emit_metric(
    ctx: ObsContext,
    name: str,
    value: float = 1.0,
    unit: str = "Count",
    dimensions: Optional[Dict[str, str]] = None,
    namespace: str = METRICS_NAMESPACE,
) -> None:
    dims = dimensions or {"service": ctx.service, "env": ctx.env}
    emf: Dict[str, Any] = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": namespace,
                    "Dimensions": [list(dims.keys())],
                    "Metrics": [{"Name": name, "Unit": unit}],
                }
            ],
        },
        **dims,
        name: value,
        "trace_id": ctx.trace_id,
    }
    _json_print(emf)

class Timer:
    def __init__(self) -> None:
        self._t0 = time.time()

    def ms(self) -> float:
        return (time.time() - self._t0) * 1000.0
