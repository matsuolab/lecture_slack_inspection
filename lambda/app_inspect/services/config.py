import os
from dataclasses import dataclass
from common.secret_manager import get_secret

@dataclass(frozen=True)
class InspectConfig:
    slack_bot_token: str
    slack_signing_secret: str
    alert_private_channel_id: str

    openai_api_key: str
    openai_model: str

    notion_api_key: str
    notion_db_id: str

    guidelines_text: str
    min_severity_to_alert: str

    use_mock_openai: bool

def load_config() -> InspectConfig:
    guidelines_text = os.getenv("GUIDELINES_TEXT", "").strip()
    if not guidelines_text:
        guidelines_text = (
            "コミュニティガイドライン（簡易版）\n"
            "- 個人情報の投稿は禁止\n"
            "- 差別/ヘイト/誹謗中傷は禁止\n"
            "- 暴力の扇動、違法行為の助長は禁止\n"
            "- 過度な性的表現は禁止\n"
            "- スパム/詐欺行為は禁止\n"
        )

    def _get_env(name: str, required: bool = False, default: str = "") -> str:
        v = os.getenv(name, default)
        if required and not v:
            raise RuntimeError(f"Missing env var: {name}")
        return v

    return InspectConfig(
        slack_bot_token=get_secret("SLACK_BOT_TOKEN_PARAM_NAME"),
        slack_signing_secret=get_secret("SLACK_SIGNING_SECRET_PARAM_NAME"),
        openai_api_key=get_secret("OPENAI_API_KEY_PARAM_NAME"),
        notion_api_key=get_secret("NOTION_API_KEY_PARAM_NAME"),
        
        alert_private_channel_id=_get_env("ALERT_PRIVATE_CHANNEL_ID", required=True),
        notion_db_id=_get_env("NOTION_DB_ID"),
        
        openai_model=_get_env("OPENAI_MODEL", default="gpt-4o-mini"),
        guidelines_text=_get_env("GUIDELINES_TEXT", default=""),
        min_severity_to_alert=_get_env("MIN_SEVERITY_TO_ALERT", default="low"),
        use_mock_openai=_get_env("USE_MOCK_OPENAI", default="false").lower() == "true",
    )