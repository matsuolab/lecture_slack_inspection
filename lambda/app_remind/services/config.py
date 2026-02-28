import os
from dataclasses import dataclass
from common.secret_manager import get_secret


@dataclass(frozen=True)
class RemindConfig:
    # Secrets
    slack_bot_token: str
    notion_api_key: str

    # Env Vars
    notion_db_id: str
    hours_threshold: int


def load_config() -> RemindConfig:
    def _get_env(name: str, default: str = "") -> str:
        return os.getenv(name, default)

    return RemindConfig(
        slack_bot_token=get_secret("SLACK_BOT_TOKEN_PARAM_NAME"),
        notion_api_key=get_secret("NOTION_API_KEY_PARAM_NAME"),

        notion_db_id=_get_env("NOTION_DB_ID"),
        hours_threshold=int(_get_env("REMINDER_HOURS_THRESHOLD", "48")),
    )
