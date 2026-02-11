import json
from common.observability import build_context, log_info, log_error, emit_metric, Timer

def handler(event, context):
    ctx = build_context(event, context, service="app_inspect")
    t = Timer()

    log_info(ctx, action="received")
    emit_metric(ctx, "events_received", 1)

    try:
        # 例：署名検証（実装済みの関数を呼ぶ）
        # verify_signature(event)
        # sigature-X-slackがSecret manager由来のSigning Secretで正しいか確認
        log_info(ctx, action="verify_signature")

        # 例：判定
        # decision = inspect_text(...)
        decision = {"violation": True, "reason": "rule_match", "confidence": 0.72}
        log_info(ctx, action="judge", decision=decision)

        # 例：private通知（ボタンvalueに trace_id を埋める）
        # value はBが読むので必須
        button_value = json.dumps({
            "trace_id": ctx.trace_id,
            "origin_channel": ctx.slack_channel_id,
            "origin_ts": ctx.slack_message_ts,
            "reason": decision.get("reason"),
        }, ensure_ascii=False)

        # post_private_alert(button_value=button_value, ...)
        log_info(ctx, action="post_private_alert")

        emit_metric(ctx, "events_processed_success", 1)
        emit_metric(ctx, "processing_latency_ms", t.ms(), unit="Milliseconds")
        return {"statusCode": 200, "body": "ok"}

    except Exception as e:
        log_error(ctx, action="process", error=e)
        emit_metric(ctx, "events_processed_failed", 1)
        emit_metric(ctx, "processing_latency_ms", t.ms(), unit="Milliseconds")
        # Slack再送対策で200返す運用ならここは200
        return {"statusCode": 200, "body": "handled"}