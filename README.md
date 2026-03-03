## Repository Structure（ディレクトリ構成）

## セットアップ

インフラ（AWS CDK）のセットアップ手順は [infra/README.md](infra/README.md) に記載しています。


本リポジトリは「アプリコード」と「インフラコード」を分離しています。（※必要に応じて随時更新）

```text
repo/
├── infra/                 # AWS CDK (API Gateway / Lambda / IAM / etc.)
├── lambda/                # Lambdaのアプリコード（関数単位）
│   ├── app_inspect/       # Lambda A: Slack投稿を受け取り検査（OpenAI等）→ アラート通知
│   ├── app_alert/         # Lambda B: 承認ボタン等を受け取り → 元投稿へ警告通知
│   ├── app_remind/        # Lambda C: Approved後48h経過した投稿にリマインド送信
│   └── common/            # 共通モジュール（Notion, SSM, Observability）
├── contracts/             # Lambda間データ契約（スキーマ, フィクスチャ）
├── tests/                 # ユニットテスト
├── evals/                 # プロンプト評価（例: promptfoo など）
└── prompts/               # 本番用プロンプト置き場（必要に応じて）




### infra/
AWSインフラ定義（CDK）を置くディレクトリです。
API Gateway / Lambda / IAM 等の構成は原則ここでコード管理します。

- 役割：インフラの再現性確保・変更履歴の可視化
- 注意：AWSコンソールでの手動変更は原則禁止（緊急時のみ、後でCDKへ反映）

### lambda/
Lambdaで動作するアプリケーションコード（Botの本体）を置くディレクトリです。

各Lambda関数は `handler.py`（エントリポイント）と `services/`（業務ロジック）で構成されます。

#### 3-Lambda アーキテクチャ

```
Slack投稿 → [Lambda A: app_inspect] → 違反検出 → Notion記録 + 管理者アラート
                                                         ↓ ボタン操作
                                          [Lambda B: app_alert] → 警告送信 or Dismiss
                                                         ↓ Approved後48h経過
                                          [Lambda C: app_remind] → 削除リマインド送信
```

| Lambda | トリガー | 役割 |
|--------|---------|------|
| **A (app_inspect)** | Slack Event API | 投稿を受信 → 3段階違反検出 → Notionログ + 管理者通知 |
| **B (app_alert)** | Slack Interactivity | 管理者ボタン操作 → 警告送信 or Dismiss → Notion更新 |
| **C (app_remind)** | EventBridge (定期) | Notionポーリング → 48h経過分にリマインド送信 |

#### common/
共通モジュール（全Lambda関数で共有）:
- `notion_client.py` - Notion API操作（作成・更新・クエリ・リマインド管理）
- `secret_manager.py` - SSM Parameter Store からシークレット取得
- `observability.py` - 構造化ログ・CloudWatch EMFメトリクス・トレーシング

### evals/
プロンプトの評価・改善（promptfoo等）を行うためのディレクトリです。

### contracts/
Lambda間のデータ契約を定義:
- `alert_button_value.schema.json` - Lambda A→B ボタン値のJSONスキーマ
- `notion_db_schema.md` - Notion DBプロパティ定義
- `fixtures/` - テスト用フィクスチャデータ


