from aws_cdk import (
    Stack,
    CfnParameter,
    CfnOutput,
    Duration,
    aws_apigateway as apigw,
    aws_lambda as _lambda,
    aws_ssm as ssm,
    aws_logs as logs,
    aws_iam as iam,
    aws_events as events,
    aws_events_targets as targets,
)
from constructs import Construct


class InfraStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # -----------------------------
        # 1. パラメータ定義 (SSMパラメータ名を受け取る)
        # -----------------------------
        slack_installation_param_prefix = CfnParameter(
            self,
            "SlackInstallationParamPrefix",
            type="String",
            default="/slack/installation",
            description="SSM prefix for Slack bot tokens per team_id.",
        )

        slack_signing_secret_param_name = CfnParameter(
            self,
            "SlackSigningSecretParamName",
            type="String",
            default="/slack/signing/secret",
            description="SSM Parameter name for Slack Signing Secret (SecureString).",
        )

        slack_client_id_param_name = CfnParameter(
            self,
            "SlackClientIdParamName",
            type="String",
            default="/slack/client/id",
            description="SSM Parameter name for Slack Client ID (SecureString).",
        )

        slack_client_secret_param_name = CfnParameter(
            self,
            "SlackClientSecretParamName",
            type="String",
            default="/slack/client/secret",
            description="SSM Parameter name for Slack Client Secret (SecureString).",
        )

        oauth_state_secret_param_name = CfnParameter(
            self,
            "OAuthStateSecretParamName",
            type="String",
            default="/slack/oauth/state",
            description="SSM Parameter name for OAuth state (SecureString).",
        )

        oauth_allowed_team_ids_param_name = CfnParameter(
            self,
            "OAuthAllowedTeamIdsParamName",
            type="String",
            default="/slack/oauth/allowed_team_ids",
            description="SSM Parameter name for allowed Slack team IDs (comma-separated).",
        )

        slack_bot_scopes = CfnParameter(
            self,
            "SlackBotScopes",
            type="String",
            default="chat:write,channels:read,channels:history,users:read,team:read",
            description="Scopes used by /slack/oauth/start. Must match your app settings",
        )

        openai_api_key_param_name = CfnParameter(
            self,
            "OpenAIApiKeyParamName",
            type="String",
            default="/openai/api/key",
            description="SSM Parameter name for OpenAI API Key (SecureString).",
        )

        notion_api_key_param_name = CfnParameter(
            self,
            "NotionApiKeyParamName",
            type="String",
            default="/notion/api/key",
            description="SSM Parameter name for Notion API Key (SecureString).",
        )

        notion_db_id = CfnParameter(
            self,
            "NotionDbId",
            type="String",
            description="Notion Database ID to store violation logs.",
        )

        notion_articles_db_id = CfnParameter(
            self,
            "NotionArticlesDbId",
            type="String",
            default="",
            description="Notion Database ID for articles master.",
        )

        notion_template_db_id = CfnParameter(
            self,
            "NotionTemplateDbId",
            type="String",
            default="",
            description="Notion Database ID for warning/reminder templates.",
        )

        notion_health_db_id = CfnParameter(
            self,
            "NotionHealthDbId",
            type="String",
            default="",
            description="Notion Database ID for Lambda health check status.",
        )

        reminder_hours_threshold = CfnParameter(
            self,
            "ReminderHoursThreshold",
            type="Number",
            default=48,
            description="Hours after warning before marking as 48h_Over.",
        )

        remind_schedule_minutes = CfnParameter(
            self,
            "RemindScheduleMinutes",
            type="Number",
            default=5,
            description="EventBridge schedule interval in minutes for Lambda D.",
        )

        # -----------------------------
        # 2. SSMパラメータARNの構築ヘルパー
        # -----------------------------
        # SecureStringはCDKデプロイ時に値を取得できないため、ARNを構築してIAM権限で使用します
        def get_param_arn(param_name: str) -> str:
            return f"arn:aws:ssm:{self.region}:{self.account}:parameter{param_name}"

        # -----------------------------
        # 3. Lambda A: Slack投稿監視 (app_inspect)
        # -----------------------------
        lambda_a = _lambda.DockerImageFunction(
            self,
            "LambdaA_AppInspect",
            code=_lambda.DockerImageCode.from_image_asset(
                directory="../lambda/",
                exclude=["app_alert", "app_oauth", "app_remind", "app_batch"],
            ),
            timeout=Duration.seconds(30),
            memory_size=512,
            log_retention=logs.RetentionDays.ONE_WEEK,
            environment={
                "SLACK_INSTALLATION_PARAM_PREFIX": slack_installation_param_prefix.value_as_string,
                "SLACK_SIGNING_SECRET_PARAM_NAME": slack_signing_secret_param_name.value_as_string,
                "OPENAI_API_KEY_PARAM_NAME": openai_api_key_param_name.value_as_string,
                "NOTION_API_KEY_PARAM_NAME": notion_api_key_param_name.value_as_string,
                "NOTION_DB_ID": notion_db_id.value_as_string,
                "NOTION_TEMPLATE_DB_ID": notion_template_db_id.value_as_string,
                "NOTION_ARTICLES_DB_ID": notion_articles_db_id.value_as_string,
                "NOTION_HEALTH_DB_ID": notion_health_db_id.value_as_string,
                "USE_MOCK_OPENAI": "false",
            },
        )

        lambda_a.node.default_child.add_property_override(
            "ImageConfig",
            {"Command": ["app_inspect.handler.lambda_handler"]},
        )

        # -----------------------------
        # 4. Lambda B: アラート対応 (app_alert)
        # -----------------------------
        lambda_b = _lambda.DockerImageFunction(
            self,
            "LambdaB_AppAlert",
            code=_lambda.DockerImageCode.from_image_asset(
                directory="../lambda/",
                exclude=["app_inspect", "app_oauth", "app_remind", "app_batch"],
            ),
            timeout=Duration.seconds(30),
            memory_size=512,
            log_retention=logs.RetentionDays.ONE_WEEK,
            environment={
                "SLACK_INSTALLATION_PARAM_PREFIX": slack_installation_param_prefix.value_as_string,
                "SLACK_SIGNING_SECRET_PARAM_NAME": slack_signing_secret_param_name.value_as_string,
                "NOTION_API_KEY_PARAM_NAME": notion_api_key_param_name.value_as_string,
                "NOTION_DB_ID": notion_db_id.value_as_string,
                "NOTION_TEMPLATE_DB_ID": notion_template_db_id.value_as_string,
                "NOTION_HEALTH_DB_ID": notion_health_db_id.value_as_string,
            },
        )

        lambda_b.node.default_child.add_property_override(
            "ImageConfig",
            {"Command": ["app_alert.handler.lambda_handler"]},
        )

        # -----------------------------
        # 5. Lambda C: OAuth対応 (app_oauth)
        # -----------------------------
        lambda_c = _lambda.DockerImageFunction(
            self,
            "LambdaC_SlackOAuth",
            code=_lambda.DockerImageCode.from_image_asset(
                directory="../lambda/",
                exclude=["app_inspect", "app_alert", "app_remind", "app_batch"],
            ),
            timeout=Duration.seconds(30),
            memory_size=512,
            log_retention=logs.RetentionDays.ONE_WEEK,
            environment={
                "SLACK_INSTALLATION_PARAM_PREFIX": slack_installation_param_prefix.value_as_string,
                "SLACK_CLIENT_ID_PARAM_NAME": slack_client_id_param_name.value_as_string,
                "SLACK_CLIENT_SECRET_PARAM_NAME": slack_client_secret_param_name.value_as_string,
                "OAUTH_STATE_SECRET_PARAM_NAME": oauth_state_secret_param_name.value_as_string,
                "OAUTH_ALLOWED_TEAM_IDS_PARAM_NAME": oauth_allowed_team_ids_param_name.value_as_string,
                "SLACK_BOT_SCOPES": slack_bot_scopes.value_as_string,
            },
        )

        lambda_c.node.default_child.add_property_override(
            "ImageConfig",
            {"Command": ["app_oauth.handler.lambda_handler"]},
        )

        # -----------------------------
        # 6. Lambda D: リマインド定期実行 (app_remind)
        # -----------------------------
        lambda_d = _lambda.DockerImageFunction(
            self,
            "LambdaD_AppRemind",
            code=_lambda.DockerImageCode.from_image_asset(
                directory="../lambda/",
                exclude=["app_inspect", "app_alert", "app_oauth", "app_batch"],
            ),
            timeout=Duration.seconds(60),
            memory_size=512,
            log_retention=logs.RetentionDays.ONE_WEEK,
            environment={
                "SLACK_INSTALLATION_PARAM_PREFIX": slack_installation_param_prefix.value_as_string,
                "NOTION_API_KEY_PARAM_NAME": notion_api_key_param_name.value_as_string,
                "NOTION_DB_ID": notion_db_id.value_as_string,
                "NOTION_TEMPLATE_DB_ID": notion_template_db_id.value_as_string,
                "NOTION_HEALTH_DB_ID": notion_health_db_id.value_as_string,
                "REMINDER_HOURS_THRESHOLD": reminder_hours_threshold.value_as_string,
            },
        )

        lambda_d.node.default_child.add_property_override(
            "ImageConfig",
            {"Command": ["app_remind.handler.lambda_handler"]},
        )

        # EventBridge定期実行ルール
        remind_rule = events.Rule(
            self,
            "RemindScheduleRule",
            schedule=events.Schedule.rate(
                Duration.minutes(remind_schedule_minutes.value_as_number)
            ),
            description="Trigger Lambda D (app_remind) periodically",
        )
        remind_rule.add_target(targets.LambdaFunction(lambda_d))

        # -----------------------------
        # 6.5 Lambda E: バッチスキャン (app_batch)
        # -----------------------------
        lambda_e = _lambda.DockerImageFunction(
            self,
            "LambdaE_AppBatch",
            code=_lambda.DockerImageCode.from_image_asset(
                directory="../lambda/",
                exclude=["app_alert", "app_oauth", "app_remind"],
            ),
            timeout=Duration.seconds(900),
            memory_size=1024,
            log_retention=logs.RetentionDays.ONE_WEEK,
            environment={
                "SLACK_INSTALLATION_PARAM_PREFIX": slack_installation_param_prefix.value_as_string,
                "OPENAI_API_KEY_PARAM_NAME": openai_api_key_param_name.value_as_string,
                "NOTION_API_KEY_PARAM_NAME": notion_api_key_param_name.value_as_string,
                "NOTION_DB_ID": notion_db_id.value_as_string,
                "NOTION_ARTICLES_DB_ID": notion_articles_db_id.value_as_string,
                "USE_MOCK_OPENAI": "false",
                "BATCH_MAX_MESSAGES_PER_INVOKE": "2000",
                "BATCH_SLEEP_MS": "100",
            },
        )

        lambda_e.node.default_child.add_property_override(
            "ImageConfig",
            {"Command": ["app_batch.handler.lambda_handler"]},
        )

        # -----------------------------
        # 7. IAM権限付与 (SSM Parameter Store)
        # -----------------------------
        installation_param_arn = (
            f"arn:aws:ssm:{self.region}:{self.account}:parameter"
            f"{slack_installation_param_prefix.value_as_string}/*"
        )

        runtime_policy = iam.PolicyStatement(
            actions=["ssm:GetParameter"],
            resources=[
                installation_param_arn,
                get_param_arn(slack_signing_secret_param_name.value_as_string),
                get_param_arn(openai_api_key_param_name.value_as_string),
                get_param_arn(notion_api_key_param_name.value_as_string),
            ],
        )

        lambda_a.add_to_role_policy(runtime_policy)
        lambda_b.add_to_role_policy(runtime_policy)
        lambda_d.add_to_role_policy(runtime_policy)
        lambda_e.add_to_role_policy(runtime_policy)

        oauth_policy = iam.PolicyStatement(
            actions=["ssm:GetParameter", "ssm:PutParameter"],
            resources=[
                f"arn:aws:ssm:{self.region}:{self.account}:parameter/slack*",
                installation_param_arn,
                get_param_arn(slack_client_id_param_name.value_as_string),
                get_param_arn(slack_client_secret_param_name.value_as_string),
                get_param_arn(oauth_state_secret_param_name.value_as_string),
                get_param_arn(oauth_allowed_team_ids_param_name.value_as_string),
            ],
        )
        lambda_c.add_to_role_policy(oauth_policy)

        # -----------------------------
        # 8. API Gateway (Slack エンドポイント)
        # -----------------------------
        api = apigw.RestApi(
            self,
            "SlackBotApi",
            rest_api_name="slack-bot-api",
            deploy_options=apigw.StageOptions(
                stage_name="prod",
                logging_level=apigw.MethodLoggingLevel.OFF,
                data_trace_enabled=False,
                metrics_enabled=True,
                throttling_rate_limit=50,
                throttling_burst_limit=100,
            ),
        )

        slack_root = api.root.add_resource("slack")
        events_resource = slack_root.add_resource("events")
        interactions = slack_root.add_resource("interactions")
        oauth = slack_root.add_resource("oauth")
        start = oauth.add_resource("start")
        callback = oauth.add_resource("callback")

        # POST /slack/events -> Lambda A
        events_resource.add_method(
            "POST",
            apigw.LambdaIntegration(lambda_a, proxy=True),
        )

        # POST /slack/interactions -> Lambda B
        interactions.add_method(
            "POST",
            apigw.LambdaIntegration(lambda_b, proxy=True),
        )

        # GET /slack/oauth/start -> Lambda C
        start.add_method(
            "GET",
            apigw.LambdaIntegration(lambda_c, proxy=True),
        )

        # GET /slack/oauth/callback -> Lambda C
        callback.add_method(
            "GET",
            apigw.LambdaIntegration(lambda_c, proxy=True),
        )

        # -----------------------------
        # 9. Outputs
        # -----------------------------
        CfnOutput(
            self,
            "SlackEventsRequestUrl",
            value=f"{api.url}slack/events",
            description="URL for Slack Event Subscription (Request URL)",
        )

        CfnOutput(
            self,
            "SlackInteractionsRequestUrl",
            value=f"{api.url}slack/interactions",
            description="URL for Slack Interactivity (Request URL)",
        )

        CfnOutput(
            self,
            "SlackOAuthStartUrl",
            value=f"{api.url}slack/oauth/start",
            description="URL for Slack OAuth installation (Start URL)",
        )

        CfnOutput(
            self,
            "SlackOAuthRedirectUrl",
            value=f"{api.url}slack/oauth/callback",
            description="Redirect URL for Slack OAuth callback",
        )