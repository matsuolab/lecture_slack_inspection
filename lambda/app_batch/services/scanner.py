import time
from typing import Iterator, Optional
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError


def iter_channel_messages(
    client: WebClient,
    channel_id: str,
    oldest: Optional[str] = None,
    latest: Optional[str] = None,
    sleep_ms: int = 100,
    page_size: int = 200,
) -> Iterator[dict]:
    """conversations.history をページネーションしながら順次 yield する。

    通常メッセージのみを返す (bot/subtype 付きはスキップ)。
    429 時は exponential backoff でリトライ。
    """
    cursor: Optional[str] = None
    while True:
        kwargs = {
            "channel": channel_id,
            "limit": page_size,
        }
        if oldest:
            kwargs["oldest"] = oldest
        if latest:
            kwargs["latest"] = latest
        if cursor:
            kwargs["cursor"] = cursor

        resp = _call_with_backoff(lambda: client.conversations_history(**kwargs))

        for msg in resp.get("messages", []):
            if msg.get("bot_id") or msg.get("subtype"):
                continue
            if not msg.get("text"):
                continue
            yield msg

        if not resp.get("has_more"):
            return

        cursor = resp.get("response_metadata", {}).get("next_cursor")
        if not cursor:
            return

        if sleep_ms > 0:
            time.sleep(sleep_ms / 1000.0)


def _call_with_backoff(fn, max_retries: int = 5):
    delay = 1.0
    for attempt in range(max_retries):
        try:
            return fn()
        except SlackApiError as e:
            status = getattr(e.response, "status_code", None)
            if status == 429:
                retry_after = int(e.response.headers.get("Retry-After", delay))
                time.sleep(retry_after)
                delay *= 2
                continue
            raise
    raise RuntimeError("Slack API call failed after retries")
