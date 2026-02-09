## Repository Structure（ディレクトリ構成）

## セットアップ

インフラ（AWS CDK）のセットアップ手順は [infra/README.md](infra/README.md) に記載しています。


本リポジトリは「アプリコード」と「インフラコード」を分離しています。（※必要に応じて随時更新）

```text
repo/
├── infra/                 # AWS CDK (API Gateway / Lambda / IAM / etc.)
├── lambda/                # Lambdaのアプリコード（関数単位）
│   ├── app_inspect/       # Slack投稿を受け取り検査（OpenAI等）→ Slack返信
│   └── app_alert/         # 承認ボタン等を受け取り → 元投稿へ勧告通知
├── evals/                 # プロンプト評価（例: promptfoo など）
└── prompts/               # 本番用プロンプト置き場（必要に応じて）




### infra/
AWSインフラ定義（CDK）を置くディレクトリです。  
API Gateway / Lambda / IAM 等の構成は原則ここでコード管理します。

- 役割：インフラの再現性確保・変更履歴の可視化
- 注意：AWSコンソールでの手動変更は原則禁止（緊急時のみ、後でCDKへ反映）

### app/
Lambdaで動作するアプリケーションコード（Botの本体）を置くディレクトリです。

#### app/handlers/
Lambdaの「入口（エンドポイント単位）」です。  
Slackからのリクエストを受け、署名検証・リクエスト解析・ACK返却など“薄い処理”を行い、必要に応じて `services/` を呼び出します。

- 例：/health、/slack/events、/slack/interactions など
- ポイント：重い処理（OpenAI推論、Notion書き込み、Slack投稿など）はなるべく `services/` 側へ寄せる

#### app/services/
業務ロジック（Botの中核処理）を置くディレクトリです。  
違反判定（OpenAI推論）、Notionの参照/ログ保存、Slackへの投稿/スレッド返信などの処理をここにまとめます。

- 役割：機能の中心・テストしやすい構造にするための分離

### evals/
プロンプトの評価・改善（promptfoo等）を行うためのディレクトリです。  
本番コード（app/）に影響を与えずに、ローカルやCIで高速にプロンプト比較・回帰テストを行います。

#### evals/prompts/
評価対象のプロンプト案を置きます（v1/v2などを並べて比較する想定、promptfooの管理想定）。


