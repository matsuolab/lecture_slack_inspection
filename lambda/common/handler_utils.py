"""両Lambdaで共有するユーティリティ"""
import os
import hmac
import time
import hashlib
from typing import Optional

import boto3
from slack_sdk import WebClient

from common.notion_client import init_notion_client

_secrets_cache: dict = {}
_slack_client: Optional[WebClient] = None

_SIGNATURE_TIMEOUT = int(os.environ.get("SLACK_SIGNATURE_TIMEOUT_SECONDS", "300"))


def get_secret(arn_env: str) -> str:
    arn = os.environ[arn_env]
    if arn in _secrets_cache:
        return _secrets_cache[arn]
    client = boto3.client("secretsmanager")
    resp = client.get_secret_value(SecretId=arn)
    value = resp["SecretString"]
    _secrets_cache[arn] = value
    return value


def verify_slack_signature(headers: dict, body: str, signing_secret: str) -> bool:
    """headersはキーが小文字化済みであること"""
    timestamp = headers.get("x-slack-request-timestamp", "")
    signature = headers.get("x-slack-signature", "")
    if not timestamp or not signature:
        return False
    try:
        if abs(time.time() - int(timestamp)) > _SIGNATURE_TIMEOUT:
            return False
    except ValueError:
        return False
    sig_basestring = f"v0:{timestamp}:{body}"
    computed = "v0=" + hmac.new(
        signing_secret.encode(), sig_basestring.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(computed, signature)


def get_slack_client() -> WebClient:
    global _slack_client
    if not _slack_client:
        token = get_secret("SLACK_BOT_TOKEN_SECRET_ARN")
        _slack_client = WebClient(token=token)
    return _slack_client


def init_notion():
    notion_key = get_secret("NOTION_API_KEY_SECRET_ARN")
    notion_db_id = os.environ.get("NOTION_VIOLATION_LOG_DB_ID", "")
    init_notion_client(notion_key, notion_db_id)
