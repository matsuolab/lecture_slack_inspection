
# LambdaB(app_alert) 入力仕様（Slack Interactivity）

## 目的
private通知の「承認ボタン」押下を受け取り、元投稿（違反投稿）へスレッド返信で削除勧告を行う。

---

## 入口（想定）
API Gateway 経由で Slack から送られる Interactivity を受信。
- Content-Type: application/x-www-form-urlencoded
- body: `payload=<JSON>`（payloadにSlack interactivity JSONが入る）

> ユニットテストでは、payload JSON本体（辞書）を直接渡してよい（fixture参照）。

---

## 処理対象のボタン

- `actions[0].action_id == "approve_violation"` 
    - Notionステータスを`approve`に更新 & 警告返信
- `actions[0].action_id == "dismiss_violation"`
    - Notionステータスを`dismiss`に更新

---

## 必須（MUST）
`actions[0].value` を JSON としてパースし、以下を取得する：
- `trace_id`
- `origin_channel`
- `origin_ts`

value(JSON)の仕様は `lambda/contracts/alert_button_value.schema.json` に準拠。

---

## 出力（Slackへ）
- `origin_channel` の `origin_ts` に対し、スレッド返信（thread_ts=origin_ts）で勧告テンプレ投稿。

---

## trace_id（E1と整合）
- LambdaB のログは、valueの `trace_id` を引き継ぐ（build_contextで抽出）。
- これにより A→B を CloudWatch 上で追跡可能にする。
