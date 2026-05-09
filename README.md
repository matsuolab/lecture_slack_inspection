## ディレクトリ構成

```
lecture_slack_inspection/
├── .github/               # GitHub Actions ワークフロー
├── contracts/             # API仕様・スキーマ・テストデータ
│   ├── specs/             # Markdown仕様書
│   ├── schemas/           # JSONスキーマ
│   └── fixtures/          # テスト用JSONフィクスチャ
├── evals/                 # プロンプト評価（promptfoo）
│   └── promptfoo/         # 評価設定・テストケース
├── infra/                 # AWS CDK インフラ定義
│   ├── stacks/            # CDKスタック
│   └── tests/             # インフラテスト
├── lambda/                # Lambda アプリケーション
│   ├── app_alert/         # 警告通知（承認ボタン）
│   ├── app_batch/         # バッチ処理（定期スキャン）
│   ├── app_inspect/       # 投稿検査（Events API）
│   ├── app_oauth/         # OAuth認証
│   ├── app_remind/        # リマインダー通知
│   ├── common/            # 共通モジュール
│   └── Dockerfile         # Lambdaコンテナ定義
├── prompt_engineering/    # プロンプトエンジニアリング
├── tests/                 # テストコード
│   ├── conftest.py        # pytest設定
│   ├── unit/              # ユニットテスト
│   └── integration/       # 統合テスト
├── requirements.txt       # 共通依存
└── README.md              # 本ファイル
```

---

## Lambda アプリケーション一覧

| Lambda | 役割 | トリガー |
|--------|------|----------|
| `app_inspect` | 投稿検査・違反判定 | Slack Events API |
| `app_alert` | 警告通知（承認ボタン） | Slack Interactive |
| `app_remind` | リマインダー送信 | スケジュール |
| `app_batch` | 一括スキャン処理 | スケジュール |
| `app_oauth` | ワークスペース認証 | Slack OAuth |

---

## 詳細ディレクトリ

### `contracts/`
API仕様・スキーマ・テストデータを管理。

| ディレクトリ | 説明 |
|-------------|------|
| `specs/` | Markdown仕様書（Block Kit、Lambda入力） |
| `schemas/` | JSONスキーマ（データ検証） |
| `fixtures/` | テスト用JSONデータ |

### `evals/`
プロンプト評価・改善。

- `promptfoo/`: 評価設定とテストケース
- 評価対象: `lambda/app_inspect/services/data/prompts/`

### `infra/`
AWSインフラ（CDK）。

- `stacks/`: API Gateway、Lambda、IAM定義
- `tests/`: CDKユニットテスト

### `lambda/`
アプリケーションコード（Bot本体）。

#### `app_inspect/` - 投稿検査
| ファイル | 説明 |
|----------|------|
| `handler.py` | Lambdaエントリーポイント |
| `services/inspection_flow.py` | 検出フロー制御 |
| `services/violation_detector.py` | 違反検出 |
| `services/moderation.py` | モデレーション処理 |
| `services/violation_transition.py` | 状態遷移 |
| `components/slack_builder.py` | Slackメッセージ構築 |
| `components/slack_event_parser.py` | イベント解析 |

#### `app_alert/` - 警告通知
| ファイル | 説明 |
|----------|------|
| `handler.py` | Lambdaエントリーポイント |
| `services/actions.py` | ボタンアクション処理 |

#### `app_remind/` - リマインダー
| ファイル | 説明 |
|----------|------|
| `handler.py` | Lambdaエントリーポイント |
| `services/reminder.py` | リマインダーロジック |

#### `app_batch/` - バッチ処理
| ファイル | 説明 |
|----------|------|
| `handler.py` | Lambdaエントリーポイント |
| `services/scanner.py` | スキャンロジック |

#### `app_oauth/` - OAuth認証
| ファイル | 説明 |
|----------|------|
| `handler.py` | Lambdaエントリーポイント |

#### `common/` - 共通モジュール
| ファイル | 説明 |
|----------|------|
| `notion_client.py` | Notion APIクライアント |
| `slack_utils.py` | Slackユーティリティ |
| `template_manager.py` | テンプレート管理 |
| `secret_manager.py` | シークレット管理 |
| `observability.py` | ログ・メトリクス |
| `health.py` | ヘルスチェック |

### `tests/`
テストコード。

| ディレクトリ | 説明 |
|-------------|------|
| `conftest.py` | pytest設定・フィクスチャー |
| `unit/` | ユニットテスト |
| `integration/` | 連携テスト・契約検証 |

---

### プロンプト更新
- 本番: `lambda/app_inspect/services/data/prompts/`
- 評価: `evals/promptfoo/`

### テスト追加
- ユニット: `tests/unit/test_<app>.py`
- 統合: `tests/integration/`
- 契約: `tests/integration/test_a_to_b_flow_contract.py`

### ログ確認
- `lambda/common/observability.py`で構造化ログ
- CloudWatch Logsで参照