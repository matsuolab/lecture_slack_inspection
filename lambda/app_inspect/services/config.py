import os
from dataclasses import dataclass
from common.secret_manager import get_secret, get_parameter_by_name

@dataclass(frozen=True)
class InspectConfig:
    slack_bot_token: str
    slack_signing_secret: str
    alert_private_channel_id: str

    openai_api_key: str
    openai_model: str

    notion_api_key: str
    notion_db_id: str
    notion_template_db_id: str
    notion_articles_db_id: str
    notion_ws_list_db_id: str

    min_severity_to_alert: str

    use_mock_openai: bool

def load_signing_secret() -> str:
    return get_secret("SLACK_SIGNING_SECRET_PARAM_NAME")

def load_config(team_id: str) -> InspectConfig:
    def _get_env(name: str, required: bool = False, default: str = "") -> str:
        v = os.getenv(name, default)
        if required and not v:
            raise RuntimeError(f"Missing env var: {name}")
        return v

    prefix = _get_env("SLACK_INSTALLATION_PARAM_PREFIX", default="/slack/installation").rstrip("/")
    slack_bot_token = get_parameter_by_name(f"{prefix}/{team_id}/bot_token")
    alert_channel_id = get_parameter_by_name(f"{prefix}/{team_id}/alert_channel_id")

    if not slack_bot_token:
        raise RuntimeError(f"Missing Slack bot token for team {team_id}.")
    
    if not alert_channel_id:
        raise RuntimeError(f"Missing alert channel ID for team {team_id}.")

    return InspectConfig(
        slack_bot_token=slack_bot_token,
        slack_signing_secret=load_signing_secret(),
        openai_api_key=get_secret("OPENAI_API_KEY_PARAM_NAME"),
        notion_api_key=get_secret("NOTION_API_KEY_PARAM_NAME"),
        alert_private_channel_id=alert_channel_id,
        notion_db_id=_get_env("NOTION_DB_ID"),
        notion_template_db_id=_get_env("NOTION_TEMPLATE_DB_ID"),
        notion_articles_db_id=_get_env("NOTION_ARTICLES_DB_ID"),
        notion_ws_list_db_id=_get_env("NOTION_WS_LIST_DB_ID"),
        openai_model=_get_env("OPENAI_MODEL"),
        min_severity_to_alert=_get_env("MIN_SEVERITY_TO_ALERT"),
        use_mock_openai=_get_env("USE_MOCK_OPENAI", default="false").lower() == "true",
    )