# AWS 構成・復旧手順メモ

Slack 投稿監視 Bot の AWS 構成記録と、ゼロから再構築（復旧）するための手順。

> **正本は CDK コード** (`infra/stacks/infra_stack.py`)。このドキュメントはそこから読み取った内容を人間向けに整理したもの。**コードと食い違ったらコードが正**。構成を変えたら本ファイルも更新すること。
>
> 記載時点の参照コミット: `infra/stacks/infra_stack.py`（`cdk synth` が通る状態）。

---

## 全体像

```
                          Slack ワークスペース
                                 │
        ┌────────────────────────┼─────────────────────────┐
        │ 投稿/編集イベント        │ ボタン押下               │ OAuth インストール
        ▼                        ▼                          ▼
  POST /slack/events     POST /slack/interactions   GET /slack/oauth/{start,callback}
        │                        │                          │
        └────────────── API Gateway (REST, stage=prod) ─────┘
                                 │ Lambda Proxy 統合
        ┌────────────┬───────────┼───────────────┬──────────────┐
        ▼            ▼           ▼               ▼              ▼
   Lambda A      Lambda B    Lambda C        Lambda D       Lambda E
  app_inspect   app_alert   app_oauth       app_remind     app_batch
   (検知)        (ボタン)    (OAuth)       (定期/EventBridge) (手動 invoke)
        │            │           │               │              │
        └────────────┴───────────┴───────┬───────┴──────────────┘
                                         │
                   ┌─────────────────────┼──────────────────────┐
                   ▼                     ▼                      ▼
          SSM Parameter Store      OpenAI API              Notion API
        (シークレット/トークン)    (違反判定)         (違反DB/条文/テンプレ/WS/health)
```

- **コンピュート**: AWS Lambda × 5（すべて **Docker イメージ**、Python 3.12）。サーバーレスのため EC2/VPC/RDS 等は無し。
- **公開エンドポイント**: API Gateway（REST API、ステージ名 `prod`）。
- **定期実行**: EventBridge ルール（Lambda D を rate で起動）。
- **状態の保存先**: Notion（違反 DB 等）。**AWS 側には永続データストアを持たない**（DynamoDB/S3 等なし）。シークレットのみ SSM Parameter Store。
- **リージョン**: `ap-northeast-1`（東京。デプロイワークフローの `aws-region` より）。

---

## Lambda 一覧

すべて共通の `lambda/Dockerfile`（`public.ecr.aws/lambda/python:3.12` ベース）を使い、CDK の `exclude` で各イメージから他アプリのディレクトリを除外してビルドする。エントリポイント（CMD）は CDK が `ImageConfig.Command` で上書きする。

| 論理ID | アプリ | トリガー | エントリポイント | timeout | memory | 主な役割 |
|---|---|---|---|---|---|---|
| `LambdaA_AppInspect` | `app_inspect` | API GW `POST /slack/events` | `app_inspect.handler.lambda_handler` | 30s | 512MB | 投稿/編集を受けて違反判定 → Notion 登録 → 管理者chアラート |
| `LambdaB_AppAlert` | `app_alert` | API GW `POST /slack/interactions` | `app_alert.handler.lambda_handler` | 30s | 512MB | 管理者のボタン操作（承認/却下/再警告/対応終了） |
| `LambdaC_SlackOAuth` | `app_oauth` | API GW `GET /slack/oauth/start`, `/callback` | `app_oauth.handler.lambda_handler` | 30s | 512MB | ワークスペース追加の OAuth フロー |
| `LambdaD_AppRemind` | `app_remind` | EventBridge（rate、既定 5 分） | `app_remind.handler.lambda_handler` | 60s | 512MB | 未対応違反のリマインド・削除検知 |
| `LambdaE_AppBatch` | `app_batch` | 手動 `aws lambda invoke` | `app_batch.handler.lambda_handler` | 900s | 1024MB | 過去メッセージの一括スキャン |

> ログ保持はすべて `ONE_WEEK`（CloudWatch Logs 1 週間）。
> Lambda E のイメージだけは `app_inspect` を含む（バッチが `app_inspect.services.moderation` を import するため。exclude が `app_alert/app_oauth/app_remind` のみ）。

---

## API Gateway

- REST API 名: `slack-bot-api`、ステージ: `prod`。
- スロットリング: rate `50` / burst `100`。メソッドログ OFF・データトレース OFF・メトリクス ON。
- ルーティング（すべて Lambda プロキシ統合）:

| メソッド・パス | 統合先 |
|---|---|
| `POST /slack/events` | Lambda A（app_inspect） |
| `POST /slack/interactions` | Lambda B（app_alert） |
| `GET /slack/oauth/start` | Lambda C（app_oauth） |
| `GET /slack/oauth/callback` | Lambda C（app_oauth） |

デプロイ後の **CfnOutput** に Slack App 設定へ貼る URL が出力される:
- `SlackEventsRequestUrl` … Event Subscriptions の Request URL
- `SlackInteractionsRequestUrl` … Interactivity の Request URL
- `SlackOAuthStartUrl` … インストール開始 URL
- `SlackOAuthRedirectUrl` … OAuth Redirect URL

---

## EventBridge（定期実行）

- ルール `RemindScheduleRule`: `events.Schedule.rate(Duration.minutes(RemindScheduleMinutes))`、ターゲットは Lambda D。
- 間隔は CFn パラメータ `RemindScheduleMinutes`（既定 `5` 分）。

---

## SSM Parameter Store（シークレット／設定の保管場所）

**AWS 側に値を持つ唯一の永続ストア。** Lambda は環境変数に「パラメータ名（パス）」だけを持ち、実行時に `secret_manager.py` 経由で SSM から値を取得（`WithDecryption=True`、プロセス内キャッシュあり）。
**CDK はパラメータの中身を作らない**（`type=String` で「名前」を受け取るだけ）。よって**復旧時はデプロイとは別に、これらの SSM パラメータを手動投入する必要がある**。

### シークレット系（SecureString として手動投入が必要）

| パラメータ名（既定パス） | 用途 | CFn パラメータ |
|---|---|---|
| `/slack/signing/secret` | Slack 署名シークレット（リクエスト検証） | `SlackSigningSecretParamName` |
| `/slack/client/id` | Slack App Client ID（OAuth） | `SlackClientIdParamName` |
| `/slack/client/secret` | Slack App Client Secret（OAuth） | `SlackClientSecretParamName` |
| `/slack/oauth/state` | OAuth state 検証用シークレット | `OAuthStateSecretParamName` |
| `/slack/oauth/allowed_team_ids` | インストール許可する team_id（カンマ区切り） | `OAuthAllowedTeamIdsParamName` |
| `/openai/api/key` | OpenAI API キー | `OpenAIApiKeyParamName` |
| `/notion/api/key` | Notion インテグレーションキー | `NotionApiKeyParamName` |

### ワークスペース別トークン（OAuth で自動 / 手動どちらも）

プレフィックス `/slack/installation`（CFn パラメータ `SlackInstallationParamPrefix`）配下に、team_id ごとに格納:

| パス | 用途 |
|---|---|
| `/slack/installation/<team_id>/bot_token` | そのワークスペースの Bot トークン |
| `/slack/installation/<team_id>/alert_channel_id` | 違反アラートを流す管理者chの ID |

> Lambda A の `load_config(team_id)` は **bot_token と alert_channel_id の両方が無いと起動時に例外**。復旧後にワークスペースを再追加するときはこの 2 つを必ず投入する。OAuth フロー（Lambda C）を通せば bot_token は自動で `PutParameter` される（alert_channel_id は別途設定が必要な場合あり）。

---

## CFn パラメータ（デプロイ時に渡す値）

`infra_stack.py` の `CfnParameter` 一覧。`default` があるものは省略可。**`NotionDbId` だけは default 無し＝必須**。

| CFn パラメータ | 既定値 | 内容 |
|---|---|---|
| `NotionDbId` | （必須） | 違反ログ DB の ID |
| `NotionArticlesDbId` | `""` | 条文マスタ DB の ID |
| `NotionTemplateDbId` | `""` | 警告/リマインドのテンプレ DB の ID |
| `NotionHealthDbId` | `""` | ヘルスチェック状態 DB の ID |
| `NotionWsListDbId` | `""` | team_id ↔ ワークスペースの対応 DB の ID |
| `OpenAIModel` | `gpt-4o-mini` | 判定に使う OpenAI モデル名 |
| `MinSeverityToAlert` | `low` | アラート発火の最小 severity |
| `ReminderHoursThreshold` | `48` | 警告から「期限超過」とみなすまでの時間 |
| `RemindScheduleMinutes` | `5` | Lambda D の起動間隔（分） |
| `SlackBotScopes` | `chat:write,channels:read,channels:history,users:read,team:read` | OAuth 要求スコープ（Slack App 設定と一致必須） |
| `SlackInstallationParamPrefix` | `/slack/installation` | ワークスペース別トークンの SSM プレフィックス |
| 各 `*ParamName` | 上表の既定パス | 各シークレットの SSM パス |

### Lambda 環境変数（参考・CDK が自動設定）

各 Lambda には上記パラメータ名や DB ID に加え、固定値として以下が入る:
- 全体: `USE_MOCK_OPENAI="false"`（A/E のみ。`true` にすると「違反」を含む文字列だけを違反扱いするモック判定になる）
- Lambda E のみ: `BATCH_MAX_MESSAGES_PER_INVOKE="2000"`, `BATCH_SLEEP_MS="100"`

---

## IAM（Lambda 実行ロールの権限）

CDK が各 Lambda に最小権限で付与（`infra_stack.py` セクション 7）:

- **Lambda A / B / D / E**（`runtime_policy`）: `ssm:GetParameter` を、署名シークレット・OpenAI キー・Notion キー・`/slack/installation/*` に対してのみ許可。
- **Lambda C**（`oauth_policy`）: `ssm:GetParameter` + **`ssm:PutParameter`**（OAuth で取得した bot_token を書き込むため）を `/slack*` 配下に許可。
- ログ出力は CDK が自動でロググループ権限を付与。

> Lambda C だけが `PutParameter` を持つ＝**新しいワークスペースのトークン書き込みは OAuth 経由のみ**が想定。

---

## デプロイ前提（ツール）

- Python 3.11 以上
- Node.js（`npx aws-cdk@2` を使うため）。CDK CLI は **`npx aws-cdk@2` に統一**（バージョン差異回避）。
- Docker（Lambda の Docker イメージをビルドするため必須）
- AWS 認証情報（対象アカウントへのデプロイ権限）。リージョンは `ap-northeast-1`。

---

## ゼロからの復旧手順

> 想定: 同一 or 新規 AWS アカウントに、同じ構成を再構築する。

### 0. 事前に手元に揃えるもの
- Slack App の Signing Secret / Client ID / Client Secret / Bot Scopes
- OpenAI API キー
- Notion インテグレーションキーと、各 DB の ID（違反 / 条文 / テンプレ / health / WS リスト）
- 復旧後に使う team_id ごとの bot_token・alert_channel_id（OAuth で再取得する場合は不要）

### 1. AWS 認証とリージョン
```bash
aws configure   # or SSO。region は ap-northeast-1
aws sts get-caller-identity   # 対象アカウントか確認
```

### 2. CDK ブートストラップ（そのアカウント/リージョンで初回のみ）
```bash
cd infra
python3 -m venv .venv && source .venv/bin/activate
pip install -U pip && pip install -r requirements.txt
npx aws-cdk@2 bootstrap aws://<ACCOUNT_ID>/ap-northeast-1
```

### 3. SSM パラメータを投入（**デプロイ前に必須**。CDK は中身を作らない）
シークレットは `SecureString` で投入する。例:
```bash
aws ssm put-parameter --name "/slack/signing/secret"   --type SecureString --value "<...>" --overwrite
aws ssm put-parameter --name "/slack/client/id"        --type SecureString --value "<...>" --overwrite
aws ssm put-parameter --name "/slack/client/secret"    --type SecureString --value "<...>" --overwrite
aws ssm put-parameter --name "/slack/oauth/state"      --type SecureString --value "<ランダム文字列>" --overwrite
aws ssm put-parameter --name "/slack/oauth/allowed_team_ids" --type SecureString --value "Txxxx,Tyyyy" --overwrite
aws ssm put-parameter --name "/openai/api/key"         --type SecureString --value "<...>" --overwrite
aws ssm put-parameter --name "/notion/api/key"         --type SecureString --value "<...>" --overwrite
```
（OAuth を使わず手動でワークスペースを足す場合のみ）team_id ごとに:
```bash
aws ssm put-parameter --name "/slack/installation/<team_id>/bot_token"        --type SecureString --value "xoxb-..." --overwrite
aws ssm put-parameter --name "/slack/installation/<team_id>/alert_channel_id" --type String       --value "Cxxxx"     --overwrite
```

### 4. synth で確認 → deploy
```bash
# Docker デーモンを起動しておくこと
npx aws-cdk@2 synth
npx aws-cdk@2 deploy --all --require-approval never \
  --parameters NotionDbId="<violations_db>" \
  --parameters NotionArticlesDbId="<articles_db>" \
  --parameters NotionTemplateDbId="<template_db>" \
  --parameters NotionHealthDbId="<health_db>" \
  --parameters NotionWsListDbId="<ws_list_db>" \
  --parameters OpenAIModel="gpt-4o-mini" \
  --parameters MinSeverityToAlert="low"
```
> GitHub Actions の `Manual Deploy (CD)`（`.github/workflows/deploy.yml`、`workflow_dispatch`）でも同じことができる。
> Notion 各 DB ID は GitHub **Secrets**、`OpenAIModel`/`MinSeverityToAlert` は **Variables** に設定済みの想定。
> AWS 認証は `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` の Secrets を使用。

### 5. デプロイ出力（CfnOutput）を Slack App に反映
`deploy` の出力 URL を Slack App 設定に貼る:
- `SlackEventsRequestUrl` → Event Subscriptions の Request URL（保存時に URL 検証が走る → Lambda A が `url_verification` を返せば緑の Verified）
- `SlackInteractionsRequestUrl` → Interactivity の Request URL
- `SlackOAuthRedirectUrl` → OAuth & Permissions の Redirect URL
- Bot Scopes が `SlackBotScopes` と一致しているか確認

### 6. 動作確認
- `docs/regression_checklist.md` の **1章（違反検知→アラート）→ 4章（ボタン）→ 8章（耐障害性）** を最低限実施。
- 新規ワークスペースは `SlackOAuthStartUrl` からインストールし、`allowed_team_ids` に team_id を入れておく。

---

## バックアップ / 注意点

- **AWS には永続データが無い**ため、AWS 側のバックアップ対象はほぼ「SSM パラメータの値」だけ。違反履歴等の業務データはすべて **Notion 側**にある（Notion 側のバックアップ運用に従う）。
- SSM の SecureString はデプロイ時に CDK から読めない設計。**パラメータを消すと Lambda が全て失敗する**ので、削除・上書きは慎重に。
- スタック削除（`cdk destroy`）しても SSM パラメータは手動投入分なので残る。再デプロイ時は手順 3 を飛ばせる場合がある（値が残っていれば）。
- リージョンを跨ぐと SSM パラメータも作り直しになる（パラメータはリージョン単位）。

---

## 関連ファイル

| ファイル | 内容 |
|---|---|
| `infra/stacks/infra_stack.py` | **構成の正本**（全 AWS リソース定義） |
| `infra/app.py` | CDK エントリポイント（スタック名 `InfraStack`） |
| `infra/cdk.json` | CDK 設定（`app: python app.py`） |
| `lambda/Dockerfile` | 全 Lambda 共通のイメージ定義 |
| `.github/workflows/deploy.yml` | 手動デプロイ（CD）ワークフロー |
| `infra/README.md` | CDK セットアップ手順（venv / synth） |
| `lambda/common/secret_manager.py` | SSM からの値取得ロジック |
