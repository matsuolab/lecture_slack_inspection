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
)
from constructs import Construct


class InfraStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # -----------------------------
        # 1. パラメータ定義 (SSMパラメータ名を受け取る)
        # -----------------------------
        # Slack Bot Token (xoxb-...)
        slack_bot_token_param_name = CfnParameter(
            self,
            "SlackBotTokenParamName",
            type="String",
            default="/slack/bot/token",
            description="SSM Parameter name for Slack Bot Token (SecureString).",
        )

        # Slack Signing Secret
        slack_signing_secret_param_name = CfnParameter(
            self,
            "SlackSigningSecretParamName",
            type="String",
            default="/slack/signing/secret",
            description="SSM Parameter name for Slack Signing Secret (SecureString).",
        )

        # OpenAI API Key
        openai_api_key_param_name = CfnParameter(
            self,
            "OpenAIApiKeyParamName",
            type="String",
            default="/openai/api/key",
            description="SSM Parameter name for OpenAI API Key (SecureString).",
        )

        # Notion API Key
        notion_api_key_param_name = CfnParameter(
            self,
            "NotionApiKeyParamName",
            type="String",
            default="/notion/api/key",
            description="SSM Parameter name for Notion API Key (SecureString).",
        )

        # 通常の環境変数パラメータ
        alert_private_channel_id = CfnParameter(
            self,
            "AlertPrivateChannelId",
            type="String",
            description="Slack private channel ID to post violation alerts (e.g., C0123...).",
        )

        notion_db_id = CfnParameter(
            self,
            "NotionDbId",
            type="String",
            description="Notion Database ID to store violation logs.",
        )

        # -----------------------------
        # 2. SSMパラメータARNの構築ヘルパー
        # -----------------------------
        # SecureStringはCDKデプロイ時に値を取得できないため、ARNを構築してIAM権限で使用します
        def get_param_arn(param_name: str) -> str:
            # パラメータ名が "/" から始まる場合の処理を含める
            clean_name = param_name if not param_name.startswith("/") else param_name[1:]
            return f"arn:aws:ssm:{self.region}:{self.account}:parameter/{clean_name}"

        # -----------------------------
        # 3. Lambda A: Slack投稿監視 (app_inspect)
        # -----------------------------
        lambda_a = _lambda.DockerImageFunction(
            self,
            "LambdaA_AppInspect",
            code=_lambda.DockerImageCode.from_image_asset(
                directory="../lambda/",
                # 不要な app_alert 等を除外してイメージを軽量化
                exclude=["app_alert", "app_alert/**", "tests", "contracts"],
            ),
            timeout=Duration.seconds(30),
            memory_size=512,
            log_retention=logs.RetentionDays.ONE_WEEK,
            environment={
                # シークレットは値ではなく「パラメータ名」を渡す
                "SLACK_BOT_TOKEN_PARAM_NAME": slack_bot_token_param_name.value_as_string,
                "SLACK_SIGNING_SECRET_PARAM_NAME": slack_signing_secret_param_name.value_as_string,
                "OPENAI_API_KEY_PARAM_NAME": openai_api_key_param_name.value_as_string,
                "NOTION_API_KEY_PARAM_NAME": notion_api_key_param_name.value_as_string,
                # 通常の値
                "ALERT_PRIVATE_CHANNEL_ID": alert_private_channel_id.value_as_string,
                "NOTION_DB_ID": notion_db_id.value_as_string,
                # モックモード有効化 (OpenAI APIを呼ばずにテスト)
                "USE_MOCK_OPENAI": "true",
            },
        )

        # 【重要】DockerfileのCMDを上書きして app_inspect 用ハンドラーを指定
        lambda_a.node.default_child.add_property_override(
            "ImageConfig",
            {"Command": ["app_inspect.handler.lambda_handler"]}
        )

        # -----------------------------
        # 4. Lambda B: アラート対応 (app_alert)
        # -----------------------------
        lambda_b = _lambda.DockerImageFunction(
            self,
            "LambdaB_AppAlert",
            code=_lambda.DockerImageCode.from_image_asset(
                directory="../lambda/",
                # 不要な app_inspect 等を除外
                exclude=["app_inspect", "app_inspect/**", "tests", "contracts"],
            ),
            timeout=Duration.seconds(30),
            memory_size=512,
            log_retention=logs.RetentionDays.ONE_WEEK,
            environment={
                "SLACK_BOT_TOKEN_PARAM_NAME": slack_bot_token_param_name.value_as_string,
                "SLACK_SIGNING_SECRET_PARAM_NAME": slack_signing_secret_param_name.value_as_string,
                "NOTION_API_KEY_PARAM_NAME": notion_api_key_param_name.value_as_string,
                "NOTION_DB_ID": notion_db_id.value_as_string,
            },
        )

        # 【重要】DockerfileのCMDを上書きして app_alert 用ハンドラーを指定
        lambda_b.node.default_child.add_property_override(
            "ImageConfig",
            {"Command": ["app_alert.handler.lambda_handler"]}
        )

        # -----------------------------
        # 5. IAM権限付与 (SSM Parameter Store)
        # -----------------------------
        # 特定のパラメータのみ読み取りを許可するポリシー
        # ssm_policy_statement = iam.PolicyStatement(
        #     actions=["ssm:GetParameter"],
        #     resources=[
        #         get_param_arn(slack_bot_token_param_name.value_as_string),
        #         get_param_arn(slack_signing_secret_param_name.value_as_string),
        #         get_param_arn(openai_api_key_param_name.value_as_string),
        #         get_param_arn(notion_api_key_param_name.value_as_string),
        #     ]
        # )
        # 一時的な切り分け用: 全てのSSMパラメータを許可
        ssm_policy_statement = iam.PolicyStatement(
            actions=["ssm:GetParameter"],
            resources=[f"arn:aws:ssm:{self.region}:{self.account}:parameter/*"]
        )

        lambda_a.add_to_role_policy(ssm_policy_statement)
        lambda_b.add_to_role_policy(ssm_policy_statement)

        # -----------------------------
        # 6. API Gateway (Slack エンドポイント)
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
        events = slack_root.add_resource("events")
        interactions = slack_root.add_resource("interactions")

        # POST /slack/events -> Lambda A
        events.add_method(
            "POST",
            apigw.LambdaIntegration(lambda_a, proxy=True),
        )

        # POST /slack/interactions -> Lambda B
        interactions.add_method(
            "POST",
            apigw.LambdaIntegration(lambda_b, proxy=True),
        )

        # -----------------------------
        # 7. Outputs
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