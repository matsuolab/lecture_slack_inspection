## Repository Structure（ディレクトリ構成）

```text
repo/
├── contracts/             # API仕様やスキーマ、テスト用データ
│   ├── specs/             # Markdown形式の仕様書
│   ├── schemas/           # JSONスキーマ
│   └── fixtures/          # テスト用フィクスチャ
├── evals/                 # プロンプト評価（例: promptfoo など）
│   └── promptfoo/         # promptfoo設定・テストケース（本番プロンプトを直接参照）
├── infra/                 # AWS CDK (API Gateway / Lambda / IAM / etc.)
│   ├── stacks/            # CDKスタック定義
│   └── tests/             # インフラコードのテスト
├── lambda/                # Lambdaのアプリコード（関数単位）
│   ├── app_alert/         # 承認ボタン等を受け取り → 元投稿へ勧告通知
│   │   ├── handler.py     # Lambdaエントリーポイント
│   │   ├── services/      # 業務ロジック（Slack通知など）
│   │   └── components/    # 再利用可能なコンポーネント（Slackフォーマットなど）
│   ├── app_inspect/       # Slack投稿を受け取り検査（OpenAI等）→ Slack返信
│   │   ├── handler.py     # Lambdaエントリーポイント
│   │   ├── services/      # 業務ロジック（違反判定など）
│   │   └── components/    # 再利用可能なコンポーネント（違反フォーマットなど）
│   └── common/            # 共通モジュール（Notionクライアント等）
├── prompts/               # 本番用プロンプト置き場（必要に応じて）
├── tests/                 # アプリケーションコードのテスト
│   ├── conftest.py        # テスト設定・共通フィクスチャー
│   ├── unit/              # ユニットテスト（各Lambdaの単体検証）
│   └── integration/       # 統合テスト（Lambda間の連携・契約検証）
└── README.md              # プロジェクト概要
```

### contracts/
API仕様やスキーマ、テスト用データを管理するディレクトリです。
- `specs/`: Markdown形式の仕様書を配置します。
- `schemas/`: JSONスキーマを配置します。
- `fixtures/`: テスト用のデータを配置します。

### evals/
プロンプト評価・改善（promptfoo等）を行うためのディレクトリです。
- `promptfoo/`: promptfooの設定ファイルとテストケースを配置します。評価には本番プロンプト（`lambda/app_inspect/services/data/prompts/`）を直接参照します。

### infra/
AWSインフラ定義（CDK）を置くディレクトリです。
- `stacks/`: CDKスタック定義（API Gateway / Lambda / IAM 等）を管理します。
- `tests/`: インフラコードのテストを配置します。

### lambda/
Lambdaで動作するアプリケーションコード（Botの本体）を置くディレクトリです。
- `app_alert/`: 承認ボタン等を受け取り、元投稿へ勧告通知を行う処理を管理します。
  - `handler.py`: Lambdaエントリーポイント。
  - `services/`: 業務ロジック（Slack通知など）を管理します。
  - `components/`: 再利用可能なコンポーネント（Slackフォーマットなど）を管理します。
- `app_inspect/`: Slack投稿を受け取り、検査（OpenAI等）を行い、Slackへ返信する処理を管理します。
  - `handler.py`: Lambdaエントリーポイント。
  - `services/`: 業務ロジック（違反判定など）を管理します。
  - `components/`: 再利用可能なコンポーネント（違反フォーマットなど）を管理します。
- `common/`: Notionクライアントや共通のロジックを管理します。

### prompts/
本番用プロンプトを配置するディレクトリです。必要に応じて使用します。

### tests/
アプリケーションコードのテストを管理するディレクトリです。
- `conftest.py`: テスト設定・共通フィクスチャーを記述します。
- `unit/`: 各Lambdaのユニットテストを配置します。
- `integration/`: Lambda間の連携・契約検証を行う統合テストを配置します。


