import json
from openai import OpenAI
from .models import ModerationResult, normalize_result

def run_moderation(client: OpenAI, model: str, guidelines: str, message_text: str) -> ModerationResult:
    system_prompt = (
        "あなたはSlack投稿のモデレーション判定器です。"
        "出力は必ずJSONのみにしてください。"
    )
    user_prompt = (
        f"【ガイドライン】\n{guidelines}\n\n"
        f"【投稿】\n{message_text}\n\n"
        "次のJSONスキーマで出力:\n"
        "{\"is_violation\": bool, \"severity\": \"low|medium|high\", "
        "\"categories\": [str], \"rationale\": str, \"recommended_reply\": str}"
    )

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0,
        )
        content = response.choices[0].message.content
        return normalize_result(json.loads(content))
    except Exception as e:
        # エラー時は安全側に倒す（違反なし扱い、またはログ出力）
        print(f"OpenAI error: {e}")
        # 仮のエラー結果を返すなどの処理も可
        return normalize_result({"is_violation": False, "rationale": f"Error: {e}"})

def encode_alert_button_value(notion_page_id: str | None, **kwargs) -> str:
    """Slackボタンのvalueに埋め込むデータをJSON化"""
    data = kwargs
    if notion_page_id:
        data["notion_page_id"] = notion_page_id
    # Slackのvalue制限(2000文字)に注意
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))