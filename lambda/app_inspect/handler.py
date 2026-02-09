import json

def handler(event, context):
    # とりあえず 200 を返す（Slack/ApiGatewayの疎通確認用）
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"ok": True}),
    }
