import os
from dataclasses import dataclass
from common.secret_manager import get_secret, get_parameter_by_name


@dataclass(frozen=True)
class BatchConfig:
    slack_bot_token: str

    openai_api_key: str
    openai_model: str

    notion_api_key: str
    notion_db_id: str
    notion_articles_db_id: str

    guidelines_text: str
    use_mock_openai: bool

    max_messages_per_invoke: int
    sleep_ms: int


def _get_env(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def load_config(team_id: str) -> BatchConfig:
    prefix = _get_env("SLACK_INSTALLATION_PARAM_PREFIX", default="/slack/installation").rstrip("/")
    slack_bot_token = get_parameter_by_name(f"{prefix}/{team_id}/bot_token")
    if not slack_bot_token:
        raise RuntimeError(f"Missing Slack bot token for team {team_id}.")

    return BatchConfig(
        slack_bot_token=slack_bot_token,
        openai_api_key=get_secret("OPENAI_API_KEY_PARAM_NAME"),
        openai_model=_get_env("OPENAI_MODEL", default="gpt-4o-mini"),
        notion_api_key=get_secret("NOTION_API_KEY_PARAM_NAME"),
        notion_db_id=_get_env("NOTION_DB_ID"),
        notion_articles_db_id=_get_env("NOTION_ARTICLES_DB_ID"),
        guidelines_text=_get_env("GUIDELINES_TEXT", default=""),
        use_mock_openai=_get_env("USE_MOCK_OPENAI", default="false").lower() == "true",
        max_messages_per_invoke=int(_get_env("BATCH_MAX_MESSAGES_PER_INVOKE", default="2000")),
        sleep_ms=int(_get_env("BATCH_SLEEP_MS", default="100")),
    )
