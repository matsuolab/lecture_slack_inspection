# lambda/app_alert/handler.py
from common.observability import build_context, log_info, log_error, emit_metric, Timer

def handler(event, context):
    ctx = build_context(event, context, service="app_alert")
    t = Timer()

    log_info(ctx, action="received_interactivity")
    emit_metric(ctx, "events_received", 1)

    try:
        # parse_apigw_body はobservability内で実施済み（ctxにtrace_idなど載る）
        log_info(ctx, action="parse_payload")

        # thread reply to origin (origin_channel/origin_ts は actions[0].value から取る想定)
        # post_thread_reply(origin_channel, origin_ts, ...)
        log_info(ctx, action="post_thread_reply")

        emit_metric(ctx, "events_processed_success", 1)
        emit_metric(ctx, "processing_latency_ms", t.ms(), unit="Milliseconds")
        return {"statusCode": 200, "body": "ok"}

    except Exception as e:
        log_error(ctx, action="process", error=e)
        emit_metric(ctx, "events_processed_failed", 1)
        emit_metric(ctx, "processing_latency_ms", t.ms(), unit="Milliseconds")
        return {"statusCode": 200, "body": "handled"}
