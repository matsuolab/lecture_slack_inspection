import os
from dataclasses import dataclass
from common.secret_manager import get_secret, get_parameter_by_name

@dataclass(frozen=True)
class AlertConfig:
    # Secrets
    slack_bot_token: str
    slack_signing_secret: str
    notion_api_key: str

    # Env Vars
    notion_db_id: str
    reply_prefix: str

def load_signing_secret() -> str:
    return get_secret("SLACK_SIGNING_SECRET_PARAM_NAME")

def load_config(team_id: str) -> AlertConfig:
    def _get_env(name: str, default: str = "") -> str:
        return os.getenv(name, default)
    
    prefix = _get_env("SLACK_INSTALLATION_PARAM_PREFIX", default="/slack/installations").rstrip("/")
    
    return AlertConfig(
        slack_bot_token=get_parameter_by_name(f"{prefix}/{team_id}/bot_token"),
        slack_signing_secret=load_signing_secret(),
        notion_api_key=get_secret("NOTION_API_KEY_PARAM_NAME"),
        
        notion_db_id=_get_env("NOTION_DB_ID"),
        reply_prefix=_get_env(
            "REPLY_PREFIX",
            default="この投稿はコミュニティガイドラインに抵触する可能性があります。内容をご確認の上、削除または修正をお願いします。",
        ),
    )