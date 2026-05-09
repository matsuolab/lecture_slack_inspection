from __future__ import annotations

from typing import Any

from common.observability import log_error

from ..services.models import SlackIdentity


def resolve_slack_identity(
    *,
    context: Any,
    slack_client: Any,
    raw_user_id: str,
    raw_channel_id: str,
    raw_team_id: str,
) -> SlackIdentity:
    profile_name = f"@{raw_user_id}" if raw_user_id else "（不明）"
    channel_name = raw_channel_id or "（不明）"
    workspace_name = raw_team_id or "（不明）"

    try:
        if raw_user_id:
            user_res = slack_client.users_info(user=raw_user_id)
            profile = user_res["user"]["profile"]
            profile_name = "@" + (
                profile.get("display_name") or profile.get("real_name") or raw_user_id
            )
    except Exception as e:
        log_error(context, action="fetch_user_name", error=e)

    try:
        if raw_channel_id:
            channel_res = slack_client.conversations_info(channel=raw_channel_id)
            channel_name = channel_res["channel"]["name"]
    except Exception as e:
        log_error(context, action="fetch_channel_name", error=e)

    try:
        if raw_team_id:
            team_res = slack_client.team_info(team=raw_team_id)
            workspace_name = team_res["team"]["name"]
    except Exception as e:
        log_error(context, action="fetch_workspace_name", error=e)

    return SlackIdentity(
        profile_name=profile_name,
        channel_name=channel_name,
        workspace_name=workspace_name,
    )