# LambdaA(app_inspect) 入力仕様（Slack Event API）

## 目的
Slack投稿（Event API）を受け取って「検出→private通知（ボタン付き）」を行う。

## 入口（想定）
API Gateway 経由で LambdaA が受信する。
- Content-Type: application/json
- body: Slack Event API のJSON文字列（APIGW proxy形式）

> ローカル/テストでは、Slack JSON本体のみを直接 handler に渡してもよい。

---

## 入力（Slack Event API body）
### 対象
- `type = "event_callback"`（通常イベント）
- `event.type = "message"`（最低限メッセージ投稿）

### 必須フィールド（MUST）
- `team_id` : string
- `event_id` : string（冪等キーとして利用）
- `event.channel` : string（投稿チャンネルID）
- `event.ts` : string（メッセージts）
- `event.text` : string（本文。空の場合あり得る）

### 任意フィールド（MAY）
- `event.user` : string（投稿者ID）
- `event.thread_ts` : string（スレッド内投稿なら）
- `event.subtype` : string（bot_message等の判別に使用）

---

## 冪等（重複イベント）方針
- Slackはリトライ等で同一イベントを複数回送る可能性がある。
- 原則 `event_id` を冪等キーとして扱う。
  - ただし冪等実装（DynamoDB等）は別タスクで、当面はログで検知してもよい。

---

## trace_id（E1と整合）
- trace_id は原則 `trace_id = "slack:<event_id>"` とする。
- private通知のボタン `value(JSON)` に trace_id を必ず含め、LambdaBが引き継ぐ。

---

## URL Verification（参考）
Slack App設定直後の検証リクエスト `type="url_verification"` は、
本仕様の検出対象外（別ハンドリング/別issue）としてよい。
