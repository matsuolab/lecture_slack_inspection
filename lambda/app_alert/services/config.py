import os
from dataclasses import dataclass
from common.secret_manager import get_secret

@dataclass(frozen=True)
class AlertConfig:
    # Secrets
    slack_bot_token: str
    slack_signing_secret: str
    notion_api_key: str

    # Env Vars
    notion_db_id: str
    reply_prefix: str

def load_config() -> AlertConfig:
    def _get_env(name: str, default: str = "") -> str:
        return os.getenv(name, default)
    
    return AlertConfig(
        slack_bot_token=get_secret("SLACK_BOT_TOKEN_PARAM_NAME"),
        slack_signing_secret=get_secret("SLACK_SIGNING_SECRET_PARAM_NAME"),
        notion_api_key=get_secret("NOTION_API_KEY_PARAM_NAME"),
        
        notion_db_id=_get_env("NOTION_DB_ID"),
        reply_prefix=_get_env(
            "REPLY_PREFIX",
            default="この投稿はコミュニティガイドラインに抵触する可能性があります。内容をご確認の上、削除または修正をお願いします。",
        ),
    )