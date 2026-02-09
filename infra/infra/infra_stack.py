from aws_cdk import (
    Stack,
    CfnParameter,
    CfnOutput,
    Duration,
    aws_apigateway as apigw,
    aws_lambda as _lambda,
    aws_secretsmanager as secretsmanager,
    aws_logs as logs,
)
from constructs import Construct


class InfraStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # -----------------------------
        # パラメータ定義
        # -----------------------------
        slack_bot_token_secret_name = CfnParameter(
            self,
            "SlackBotTokenSecretName",
            type="String",
            default="slack/bot/token",
            description="Secrets Manager secret name that stores Slack Bot Token (xoxb-...).",
        )

        slack_signing_secret_name = CfnParameter(
            self,
            "SlackSigningSecretName",
            type="String",
            default="slack/signing/secret",
            description="Secrets Manager secret name that stores Slack Signing Secret.",
        )

        openai_api_key_secret_name = CfnParameter(
            self,
            "OpenAIApiKeySecretName",
            type="String",
            default="openai/api/key",
            description="Secrets Manager secret name that stores OpenAI API key.",
        )

        # -----------------------------
        # シークレットキー参照定義
        # -----------------------------
        slack_bot_token_secret = secretsmanager.Secret.from_secret_name_v2(
            self,
            "SlackBotTokenSecret",
            slack_bot_token_secret_name.value_as_string,
        )

        slack_signing_secret = secretsmanager.Secret.from_secret_name_v2(
            self,
            "SlackSigningSecret",
            slack_signing_secret_name.value_as_string,
        )

        openai_api_key_secret = secretsmanager.Secret.from_secret_name_v2(
            self,
            "OpenAIApiKeySecret",
            openai_api_key_secret_name.value_as_string,
        )


        # -----------------------------
        # Lambda A: Slack投稿処理 -> OpenAI -> Slack返信
        # -----------------------------
        lambda_a = _lambda.Function(
            self,
            "LambdaA_SlackPostProcessor",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="handler.handler",  # 例: lambda_a/app.py の handler
            code=_lambda.Code.from_asset("../lambda/app_inspect"),
            timeout=Duration.seconds(30),
            memory_size=512,
            log_retention=logs.RetentionDays.ONE_WEEK,
            environment={
                "SLACK_BOT_TOKEN_SECRET_ARN": slack_bot_token_secret.secret_arn,
                "SLACK_SIGNING_SECRET_ARN": slack_signing_secret.secret_arn,
                "OPENAI_API_KEY_SECRET_ARN": openai_api_key_secret.secret_arn,
            },
        )

        # -----------------------------
        # Lambda B: 違反投稿勧告通知(承認ボタン等) -> Slack
        # -----------------------------
        lambda_b = _lambda.Function(
            self,
            "LambdaB_ViolationNotice",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="handler.handler",  # 例: lambda_b/app.py の handler
            code=_lambda.Code.from_asset("../lambda/app_alert"),
            timeout=Duration.seconds(30),
            memory_size=512,
            log_retention=logs.RetentionDays.ONE_WEEK,
            environment={
                "SLACK_BOT_TOKEN_SECRET_ARN": slack_bot_token_secret.secret_arn,
                "SLACK_SIGNING_SECRET_ARN": slack_signing_secret.secret_arn,
            },
        )

        # Sシークレットキーの読み取り権限をLambdaに付与
        slack_bot_token_secret.grant_read(lambda_a)
        slack_signing_secret.grant_read(lambda_a)
        openai_api_key_secret.grant_read(lambda_a)

        slack_bot_token_secret.grant_read(lambda_b)
        slack_signing_secret.grant_read(lambda_b)

        # -----------------------------
        # API Gateway (Slack エンドポイント)
        # -----------------------------
        api = apigw.RestApi(
            self,
            "SlackBotApi",
            rest_api_name="slack-bot-api",
            deploy_options=apigw.StageOptions(
                stage_name="prod",
                logging_level=apigw.MethodLoggingLevel.INFO,
                data_trace_enabled=False,
                metrics_enabled=True,
                throttling_rate_limit=50,
                throttling_burst_limit=100,
            ),
        )

        slack_root = api.root.add_resource("slack")
        events = slack_root.add_resource("events")
        interactions = slack_root.add_resource("interactions")

        events.add_method(
            "POST",
            apigw.LambdaIntegration(lambda_a, proxy=True),
        )

        interactions.add_method(
            "POST",
            apigw.LambdaIntegration(lambda_b, proxy=True),
        )

        # -----------------------------
        # Outputs
        # -----------------------------
        CfnOutput(
            self,
            "SlackEventsRequestUrl",
            value=f"{api.url}slack/events",
        )
        CfnOutput(
            self,
            "SlackInteractionsRequestUrl",
            value=f"{api.url}slack/interactions",
        )
