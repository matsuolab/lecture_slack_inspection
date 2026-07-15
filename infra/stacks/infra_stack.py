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

        notion_ws_list_db_id = CfnParameter(
            self,
            "NotionWsListDbId",
            type="String",
            default="",
            description="Notion Database ID for WS list master (team_id <-> workspace mapping).",
        )
            
        openai_model = CfnParameter(
            self,
            "OpenAIModel",
            type="String",
            default="gpt-4o-mini",
            description="OpenAI model name for content moderation.",
        )

        min_severity_to_alert = CfnParameter(
            self,
            "MinSeverityToAlert",
            type="String",
            default="low",
            description="Minimum severity level to trigger alert.",
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
                exclude=["app_alert", "app_oauth", "app_remind", "app_batch", "app_admin"],
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
                "NOTION_WS_LIST_DB_ID": notion_ws_list_db_id.value_as_string,
                "OPENAI_MODEL": openai_model.value_as_string,
                "MIN_SEVERITY_TO_ALERT": min_severity_to_alert.value_as_string,
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
                exclude=["app_inspect", "app_oauth", "app_remind", "app_batch", "app_admin"],
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
                exclude=["app_inspect", "app_alert", "app_remind", "app_batch", "app_admin"],
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
                exclude=["app_inspect", "app_alert", "app_oauth", "app_batch", "app_admin"],
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
                exclude=["app_alert", "app_oauth", "app_remind", "app_admin"],
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
        # 6.6 Lambda F: OAuth許可リスト管理 (app_admin)
        # -----------------------------
        lambda_f = _lambda.DockerImageFunction(
            self,
            "LambdaF_AppAdmin",
            code=_lambda.DockerImageCode.from_image_asset(
                directory="../lambda/",
                exclude=["app_inspect", "app_alert", "app_oauth", "app_remind", "app_batch"],
            ),
            timeout=Duration.seconds(30),
            memory_size=512,
            log_retention=logs.RetentionDays.ONE_WEEK,
            environment={
                "OAUTH_ALLOWED_TEAM_IDS_PARAM_NAME": oauth_allowed_team_ids_param_name.value_as_string,
            },
        )

        lambda_f.node.default_child.add_property_override(
            "ImageConfig",
            {"Command": ["app_admin.handler.lambda_handler"]},
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

        # app_admin: 許可リストのSSMパラメータ1個だけに限定した最小スコープ
        admin_policy = iam.PolicyStatement(
            actions=["ssm:GetParameter", "ssm:PutParameter"],
            resources=[
                get_param_arn(oauth_allowed_team_ids_param_name.value_as_string),
            ],
        )
        lambda_f.add_to_role_policy(admin_policy)

        # /admin/oauth-allowlist を呼び出すための専用IAMロール。
        # x-api-key はAWS公式にも「認証・認可には使わない」ことが明記されているため
        # (usage planはrate limiting/利用者識別用の仕組みでしかない)、
        # IAM(SigV4)で呼び出し元を限定する。実際に呼び出す人/システムはこのロールをAssumeする。
        admin_caller_role = iam.Role(
            self,
            "OAuthAllowlistAdminCallerRole",
            assumed_by=iam.AccountPrincipal(self.account),
            description="Assume this role (SigV4) to call POST /admin/oauth-allowlist",
        )

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
                method_options={
                    "/admin/oauth-allowlist/POST": apigw.MethodDeploymentOptions(
                        throttling_rate_limit=1,
                        throttling_burst_limit=5,
                    ),
                },
            ),
        )

        slack_root = api.root.add_resource("slack")
        events_resource = slack_root.add_resource("events")
        interactions = slack_root.add_resource("interactions")
        oauth = slack_root.add_resource("oauth")
        start = oauth.add_resource("start")
        callback = oauth.add_resource("callback")

        admin = api.root.add_resource("admin")
        oauth_allowlist = admin.add_resource("oauth-allowlist")

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

        # POST /admin/oauth-allowlist -> Lambda F
        # x-api-keyは「認証・認可の手段として使うべきではない」とAWS公式ドキュメントが
        # 明記しているため(usage plan/api keyは利用量トラッキング用途のみ)、
        # IAM(SigV4)で呼び出し元をadmin_caller_roleに限定する。
        oauth_allowlist_method = oauth_allowlist.add_method(
            "POST",
            apigw.LambdaIntegration(lambda_f, proxy=True),
            authorization_type=apigw.AuthorizationType.IAM,
        )

        admin_caller_role.add_to_policy(
            iam.PolicyStatement(
                actions=["execute-api:Invoke"],
                resources=[oauth_allowlist_method.method_arn],
            )
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

        CfnOutput(
            self,
            "OAuthAllowlistAdminUrl",
            value=f"{api.url}admin/oauth-allowlist",
            description="URL for admin allowlist registration (requires SigV4 request signed as OAuthAllowlistAdminCallerRole)",
        )

        CfnOutput(
            self,
            "OAuthAllowlistAdminCallerRoleArn",
            value=admin_caller_role.role_arn,
            description="IAM role to assume (sts:AssumeRole) before calling /admin/oauth-allowlist with SigV4",
        )
