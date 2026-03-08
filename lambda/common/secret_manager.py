import boto3
import os

_ssm = boto3.client("ssm")
_secrets_cache = {}

def get_parameter_by_name(param_name: str, with_decryption: bool = True) -> str:
    """
    SSM Parameter Storeからパラメータ名で値を取得する
    """
    if not param_name:
        return ""
    cache_key = f"{param_name}:{with_decryption}"
    if cache_key in _secrets_cache:
        return _secrets_cache[cache_key]
    
    try:
        resp = _ssm.get_parameter(Name=param_name, WithDecryption=with_decryption)
        value = resp.get("Parameter", {}).get("Value", "")
        _secrets_cache[cache_key] = value
        return value
    except Exception as e:
        print(f"Failed to fetch parameter {param_name}: {e}")
        return ""
    
def get_parameter_by_name_no_cache(param_name: str, with_decryption: bool = True) -> str:
    """
    キャッシュを使用せずにSSM Parameter Storeからパラメータ名で値を取得する
    """
    if not param_name:
        return ""
    try:
        resp = _ssm.get_parameter(Name=param_name, WithDecryption=with_decryption)
        value = resp.get("Parameter", {}).get("Value", "")
        return value
    except Exception as e:
        print(f"Failed to fetch parameter {param_name}: {e}")
        return ""

def get_secret(secret_name_env_key: str) -> str:
    """
    環境変数(Key)からパラメータ名を取得し、SSM Parameter Storeから値を取得する
    """
    # 環境変数には "SLACK_BOT_TOKEN_PARAM_NAME" のようにパラメータ名が入っている想定
    param_name = os.getenv(secret_name_env_key)
    if not param_name:
        return ""
    
    return get_parameter_by_name(param_name, with_decryption=True)

def get_secret_no_cache(secret_name_env_key: str) -> str:
    """
    キャッシュを使用せずに環境変数(Key)からパラメータ名を取得し、SSM Parameter Storeから値を取得する
    """
    param_name = os.getenv(secret_name_env_key)
    if not param_name:
        return ""
    
    return get_parameter_by_name_no_cache(param_name, with_decryption=True)

def put_secure_parameter(param_name:str, value:str) -> None:
    """
    SSM Parameter StoreにSecureStringとしてパラメータを保存する
    """
    try:
        _ssm.put_parameter(
            Name=param_name,
            Value=value,
            Type="SecureString",
            Overwrite=True
        )
        _secrets_cache.clear()
        print(f"Parameter {param_name} has been stored successfully.")
    except Exception as e:
        print(f"Failed to store parameter {param_name}: {e}")


