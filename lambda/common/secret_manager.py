import boto3
import os

_secrets_cache = {}

def get_secret(secret_name_env_key: str) -> str:
    """
    環境変数(Key)からパラメータ名を取得し、SSM Parameter Storeから値を取得する
    """
    # 環境変数には "SLACK_BOT_TOKEN_PARAM_NAME" のようにパラメータ名が入っている想定
    param_name = os.getenv(secret_name_env_key)
    if not param_name:
        return ""
    
    # キャッシュ確認
    if param_name in _secrets_cache:
        return _secrets_cache[param_name]

    # ★変更点: SSMクライアントを使用
    client = boto3.client("ssm")
    try:
        # ★変更点: get_parameter を使用 (WithDecryption=True で復号)
        resp = client.get_parameter(Name=param_name, WithDecryption=True)
        if "Parameter" in resp and "Value" in resp["Parameter"]:
            secret_value = resp["Parameter"]["Value"]
            _secrets_cache[param_name] = secret_value
            return secret_value
    except Exception as e:
        print(f"Failed to fetch parameter {param_name}: {e}")
        return ""
    
    return ""