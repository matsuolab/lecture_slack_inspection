import aws_cdk as core
import aws_cdk.assertions as assertions

from stacks.infra_stack import InfraStack


def _make_template() -> assertions.Template:
    """共通のスタックテンプレートを生成するヘルパー"""
    app = core.App()
    stack = InfraStack(app, "infra")
    return assertions.Template.from_stack(stack)


def test_lambda_functions_created():
    """Lambda関数が2つ（DockerImage形式）作成されていることを確認"""
    template = _make_template()
    template.resource_count_is("AWS::Lambda::Function", 2)
    template.has_resource_properties("AWS::Lambda::Function", {
        "PackageType": "Image",
        "ImageConfig": {
            "Command": ["app_inspect.handler.lambda_handler"]
        },
    })
    template.has_resource_properties("AWS::Lambda::Function", {
        "PackageType": "Image",
        "ImageConfig": {
            "Command": ["app_alert.handler.lambda_handler"]
        },
    })


def test_lambda_a_environment_variables():
    """Lambda A (app_inspect) の環境変数にSSMパラメータ名が設定されていることを確認"""
    template = _make_template()
    template.has_resource_properties("AWS::Lambda::Function", {
        "PackageType": "Image",
        "ImageConfig": {"Command": ["app_inspect.handler.lambda_handler"]},
        "Environment": {
            "Variables": assertions.Match.object_like({
                "SLACK_BOT_TOKEN_PARAM_NAME": "/slack/bot/token",
                "SLACK_SIGNING_SECRET_PARAM_NAME": "/slack/signing/secret",
                "OPENAI_API_KEY_PARAM_NAME": "/openai/api/key",
                "NOTION_API_KEY_PARAM_NAME": "/notion/api/key",
            })
        },
    })


def test_apigw_created():
    """API Gatewayが正しい名前・エンドポイント設定で作成されていることを確認"""
    template = _make_template()
    # CDK RestApi のデフォルトエンドポイントタイプは EDGE
    template.has_resource_properties("AWS::ApiGateway::RestApi", {
        "Name": "slack-bot-api",
        "EndpointConfiguration": {
            "Types": ["EDGE"]
        }
    })
