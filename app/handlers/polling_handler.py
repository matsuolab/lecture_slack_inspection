"""
ポーリング方式のLambdaハンドラー
EventBridge (1分間隔) でトリガー。Events API権限取得後にリアルタイム方式へ移行予定。
"""
import os
import json
import time
import logging
from datetime import datetime, timedelta
from typing import Optional

import boto3
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from app.services import (
    ViolationDetector, init_notion_client,
    check_duplicate_violation, create_violation_log,
    notify_admin, get_user_name, send_warning_reply,
    query_by_status, update_violation_status,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Lambda container cache
_secrets = None
_slack = None
_detector = None


def get_secrets() -> dict:
    global _secrets
    if _secrets:
        return _secrets

    name = os.environ.get("SECRET_NAME", "aie09bot/secrets")
    region = os.environ.get("AWS_REGION", "ap-northeast-1")
    client = boto3.client("secretsmanager", region_name=region)

    try:
        resp = client.get_secret_value(SecretId=name)
        _secrets = json.loads(resp["SecretString"])
        logger.info("Secrets loaded")
        return _secrets
    except Exception as e:
        logger.error(f"Failed to get secrets: {e}")
        raise


def get_slack_client() -> WebClient:
    global _slack
    if not _slack:
        _slack = WebClient(token=get_secrets()["SLACK_BOT_TOKEN"])
    return _slack


def get_detector() -> ViolationDetector:
    global _detector
    if not _detector:
        secrets = get_secrets()
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        _detector = ViolationDetector(
            openai_api_key=secrets["OPENAI_API_KEY"],
            articles_path=os.path.join(base, "services", "data", "articles.json"),
            ng_patterns_path=os.path.join(base, "services", "data", "ng_patterns.json"),
        )
    return _detector


def get_channels(client: WebClient, monitor_ids: Optional[list] = None) -> list:
    channels = []

    if monitor_ids:
        for cid in monitor_ids:
            try:
                resp = client.conversations_info(channel=cid)
                name = resp.get("channel", {}).get("name", cid)
                channels.append({"id": cid, "name": name})
            except SlackApiError as e:
                logger.warning(f"Channel {cid} info failed: {e}")
                channels.append({"id": cid, "name": cid})
        return channels

    try:
        cursor = None
        while True:
            resp = client.conversations_list(
                types="public_channel", exclude_archived=True, limit=100, cursor=cursor
            )
            for ch in resp.get("channels", []):
                if ch.get("is_member"):
                    channels.append({"id": ch["id"], "name": ch["name"]})
            if not resp.get("has_more"):
                break
            cursor = resp.get("response_metadata", {}).get("next_cursor")
        logger.info(f"Found {len(channels)} channels")
    except SlackApiError as e:
        logger.error(f"Channel list error: {e}")
    return channels


def detect_course(channel_name: str) -> Optional[str]:
    if not channel_name:
        return None
    name = channel_name.lower()
    if "gci" in name:
        return "GCI"
    if "dl" in name:
        return "DL"
    if "llm" in name:
        return "LLM"
    return None


def check_channel(
    client: WebClient, detector: ViolationDetector, channel: dict,
    bot_user_id: str, admin_channel_id: Optional[str], lookback_minutes: int = 2,
) -> int:
    cid, cname = channel["id"], channel["name"]
    violations = 0
    oldest = (datetime.now() - timedelta(minutes=lookback_minutes)).timestamp()

    try:
        resp = client.conversations_history(channel=cid, oldest=str(oldest), limit=100)
        messages = resp.get("messages", [])
        logger.info(f"#{cname}: {len(messages)} messages")

        for msg in messages:
            if msg.get("subtype") or msg.get("user") == bot_user_id:
                continue
            text = msg.get("text", "")
            if not text:
                continue

            ts = msg.get("ts")
            if check_duplicate_violation(ts):
                continue

            result = detector.detect(text, course=detect_course(cname))
            if result.is_violation:
                violations += 1
                logger.warning(f"Violation in #{cname}: {result.category}")
                handle_violation(client, msg, cid, cname, result, admin_channel_id)

    except SlackApiError as e:
        if e.response.get("error") != "not_in_channel":
            logger.error(f"Message fetch error ({cname}): {e}")

    return violations


def handle_violation(
    client: WebClient, message: dict, channel_id: str,
    channel_name: str, result, admin_channel_id: Optional[str],
):
    uid = message.get("user")
    ts = message.get("ts")
    text = message.get("text", "")
    uname = get_user_name(client, uid)
    link = f"https://slack.com/archives/{channel_id}/p{ts.replace('.', '')}"

    try:
        create_violation_log(
            post_id=ts, post_content=text, user_id=uid, channel=f"#{channel_name}",
            result="違反", method=result.method, article_id=result.article_id,
            confidence=result.confidence, reason=result.reason, post_link=link,
        )
        logger.info("Logged to Notion")
    except Exception as e:
        logger.error(f"Notion log failed: {e}")

    if admin_channel_id:
        notify_admin(
            client=client, user_id=uid, user_name=uname,
            channel_id=channel_id, channel_name=channel_name, message_ts=ts,
            text=text, result=result, admin_channel_id=admin_channel_id,
        )


def process_pending_warnings(client: WebClient) -> int:
    """「警告送信」ステータスの違反を処理"""
    logs = query_by_status("警告送信")
    logger.info(f"Found {len(logs)} pending warnings")

    processed = 0
    for log in logs:
        if send_warning_reply(client, log):
            update_violation_status(
                page_id=log["page_id"],
                status="警告送信済",
                warning_sent_at=datetime.now()
            )
            processed += 1
            logger.info(f"Warning sent and status updated: {log['page_id']}")
        else:
            logger.error(f"Failed to send warning: {log['page_id']}")

    return processed


def lambda_handler(event, context):
    logger.info("Lambda invoked")
    start = time.time()

    try:
        secrets = get_secrets()
        init_notion_client(secrets["NOTION_API_KEY"], secrets["NOTION_VIOLATION_LOG_DB_ID"])

        client = get_slack_client()
        auth = client.auth_test()
        bot_id = auth.get("user_id")
        logger.info(f"Bot: {auth.get('user')}")

        admin_ch = secrets.get("SLACK_ADMIN_CHANNEL_ID")
        monitor_str = secrets.get("SLACK_MONITOR_CHANNEL_IDS", "")
        monitor_ids = [c.strip() for c in monitor_str.split(",") if c.strip()] or None

        lookback = int(os.environ.get("LOOKBACK_MINUTES", "2"))
        detector = get_detector()
        channels = get_channels(client, monitor_ids)

        total = 0
        for ch in channels:
            total += check_channel(client, detector, ch, bot_id, admin_ch, lookback)

        # 「警告送信」ステータスの処理
        warnings_sent = process_pending_warnings(client)

        elapsed = time.time() - start
        logger.info(f"Done in {elapsed:.2f}s. Violations: {total}, Warnings: {warnings_sent}")

        return {
            "statusCode": 200,
            "body": json.dumps({
                "message": "OK",
                "channels_checked": len(channels),
                "violations_found": total,
                "warnings_sent": warnings_sent,
                "elapsed_seconds": round(elapsed, 2),
            })
        }

    except Exception as e:
        logger.error(f"Lambda error: {e}", exc_info=True)
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}
