# LambdaA(app_inspect) 出力仕様：private通知（Block Kit）

## 目的
違反（または要確認）と判定した投稿を、運営用 private チャンネルへ通知する。
通知には「承認ボタン」を含め、押下時に LambdaB(app_alert) がスレッド返信を実行する。

---

## 通知先
- private channel ID は環境変数等で指定（例: `ALERT_PRIVATE_CHANNEL_ID`）

---

## ボタン仕様（A -> B 契約）
### action_id（固定）

- `approve_violation`(違反投稿として運営側に承認された)
- `dismiss_violation`(違反投稿ではないとして運営側に承認された)

### value（固定：JSON文字列）
- `contracts/schemas/alert_button_value.schema.json` に準拠
- 最低限 MUST:
  - `trace_id`
  - `origin_channel`
  - `origin_ts`

---

## Block Kit 例（Slack API: chat.postMessage）
> blocks の中身は実装側で変更可。ただし **action_id/value** は固定。

```json
{
  "text": "🚨 違反の可能性がある投稿を検出しました",
  "blocks": [
    {
      "type": "section",
      "text": { "type": "mrkdwn", "text": "🚨 *違反の可能性がある投稿を検出しました* \n・理由: `spam` \n・trace_id: `slack:EvXXXX`" }
    },
    {
      "type": "context",
      "elements": [
        { "type": "mrkdwn", "text": "origin_channel: `C123` / origin_ts: `1700000000.12345`" }
      ]
    },
    { "type": "divider" },
    {
      "type": "actions",
      "elements": [
        {
          "type": "button",
          "text": { "type": "plain_text", "text": "削除勧告を送る", "emoji": true },
          "style": "danger",
          "action_id": "approve_violation",
          "value": "{\"version\":\"v1\",\"trace_id\":\"slack:EvXXXX\",\"origin_channel\":\"C123\",\"origin_ts\":\"1700000000.12345\",\"reason\":\"spam\",\"policy_refs\":[\"p3-2\"]}"
        }
      ]
    }
  ]
}
