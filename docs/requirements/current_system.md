# 現状の仕様（As-Is）

- 作成日: 2026-11-11
- 基準: コミット `cae84fc`（ブランチ `feat/decisions-detection`）。行番号はすべてこのコミットで数えた（`python3 docs/requirements/evidence/check_line_refs.py cae84fc <文書>` で照合できる）。**本番にデプロイされているのは、これより前の版**（判定方式の移行は未デプロイ。`docs/research/20261013_detection_migration_todo.md` の手順 3〜7 が未）
- 方針: コードが実際にどう動くかだけを書く。あるべき姿は [要件定義](system_requirements.md)、問題点は [課題の一覧](issues.md) に分ける
- 記号: 【確認済み】コードで確認、【要確認】本番の設定・実物・実測で確かめる必要がある

## 1. 目的と範囲

松尾研 DX プロジェクトの受講生用 Slack ワークスペース（複数）で、規約に反する投稿を見つけ、運営の管理者に知らせ、管理者の判断で投稿者に警告し、その後の対応を Notion で追跡する。

| 範囲 | 内容 |
|---|---|
| 監視する投稿 | Bot がインストールされたワークスペースの、Bot が参加している公開チャンネルの新規投稿と編集（2.2） |
| 監視しない投稿 | Bot・アプリの投稿、`subtype` の付いた投稿（ファイル付きなど。2.2）、本文が空の投稿、`real_name` に「松尾研」を含むユーザーの投稿、非公開チャンネル・DM（スコープに `groups:history`・`im:history` が無い。【要確認】Slack アプリの Event Subscriptions の設定はリポジトリに無い） |
| 判定の基準 | `lambda/common/data/articles.json`（31 条）＋ワークスペースごとのローカルルール（Notion 条文マスタ）＋判定方針のプロンプト |
| 人が判断すること | 警告を送るか（「警告を送る」／「対応不要」）、48 時間後に再警告するか（「再警告」／「対応終了」） |

## 2. 構成

### 2.1 コンポーネント

| Lambda | 起動 | 時間の上限 | 役割 |
|---|---|---|---|
| A `app_inspect` | API Gateway `POST /slack/events`（同期） | 30 秒 | 投稿の判定 → Notion に記録 → 管理者チャンネルに通知 |
| B `app_alert` | API Gateway `POST /slack/interactions`（同期） | 30 秒 | 管理者のボタン操作 |
| C `app_oauth` | API Gateway `GET /slack/oauth/start`・`/callback` | 30 秒 | ワークスペースへのインストール |
| D `app_remind` | EventBridge（既定 5 分ごと） | 60 秒 | 48 時間経過の検出、投稿の削除の検出 |
| E `app_batch` | 手動（`aws lambda invoke`） | 900 秒 | 過去の投稿の一括判定 |
| F `app_admin` | API Gateway `POST /admin/oauth-allowlist`（IAM 認証） | 30 秒 | インストールを許可するワークスペースの追加 |

- すべて同じ Docker イメージ（Python 3.12）から、他のアプリのディレクトリを除いて作る。E だけは A を含む（判定を共有するため）
- AWS 側に業務データの保存先は無い。状態はすべて Notion、秘密情報とワークスペースごとのトークンは SSM Parameter Store
- 監視: CloudWatch Logs（保持 1 週間）、EMF のメトリクス、Notion のヘルスチェック DB。CloudWatch のアラーム・DLQ は無い【確認済み】

### 2.2 投稿の受け取り（Lambda A）

`handler.py` → `slack_event_parser.py` の順で、次のどれかに当たれば判定せずに返す。

| 順 | 条件 | 応答 |
|---|---|---|
| 1 | `x-slack-retry-num` ヘッダーがある（Slack の再送） | 200（何もしない） |
| 2 | 署名が不正 | 401 |
| 3 | JSON でない | 400 |
| 4 | `url_verification` | challenge を返す |
| 5 | `team_id` が無い | 400 |
| 6 | `event_callback` でない、`event.type` が `message` でない | 200 |
| 7 | `subtype` が無い投稿で、`bot_id` がある、または本文が空 | 200 |
| 8 | `subtype` が `message_changed` 以外（`file_share`・`thread_broadcast` なども含む） | 200 |
| 9 | 編集で、Bot の投稿、編集後が空、本文が変わっていない | 200 |
| 10 | `event.user` があり、`users.info` の `real_name` に「松尾研」を含む | 200 |

- 1 の再送の判定は署名の確認より前。再送はすべて捨てるので、最初の実行が失敗した投稿は二度と判定されない
- 10 は `event.user` だけを見る（`app_inspect/handler.py:136`）。編集イベント（`message_changed`）は `event` の直下に `user` が無い（Slack の公式のペイロード例）ので、編集ではスタッフの除外が効かず、判定に進む【確認済み】
- 10 の `users.info` が失敗すると例外になり、その投稿は判定されない（全体の except で 200）
- ワークスペースの設定（`<prefix>/<team_id>/bot_token` と `alert_channel_id`）が無いと例外になり、その投稿は判定されない

### 2.3 判定（`violation_detector.py`）

`DETECTION_MODE`（CDK パラメータ `DetectionMode`、既定 `auto`）で方式を選ぶ。

| 方式 | 選ばれる条件 | 中身 | Notion の「検出方法」 |
|---|---|---|---|
| `llm_score` | `auto` で `OPENAI_MODEL` が `gpt-4o-mini` で始まる | `judge_violation.txt` で 0〜100 の点数を出させ、51 以上で違反 | `LLM` |
| `decisions` | `auto` でそれ以外 | 1 段目: Decisions API（モデル `gpt-6-luna` を直接指定）で warn / safe の確率と条文の確率。P(warn) ≥ 0.5 で違反。2 段目（違反のときだけ）: `OPENAI_MODEL` で上位 3 条文から 1 つ選ぶ | `Decisions`。1 段目が例外なら予備経路で `LLM-fallback` |
| `luna` | 明示したとき | 1 段目を `OPENAI_MODEL` の Chat Completions で warn / safe ＋候補 3 条文。2 段目は同じ | `Luna` |

- 条文: `articles.json` の 31 条＋ローカルルール（Notion 条文マスタで `workspace` にそのワークスペースを含み `有効` = true）。ローカルルールの取得の失敗はログに残して無視する
- タイムアウト: Decisions API は 10 秒（`requests`、再試行なし）。Chat Completions は指定なし（openai SDK の既定: 読み取り 600 秒、再試行 2 回）【確認済み】
- **失敗時**【確認済み】:
  - 違反かどうかを決める段（`llm_score` の 1 回、`decisions` の Decisions API と予備経路の両方、`luna` の 1 段目）が例外になると「違反なし」（`is_violation=False`）になる（`violation_detector.py:262-269, 516-523`、`moderation.py:44-50`）。メトリクスもエラーのログ（`log_error`）も出さない
  - 2 段目（条文の選択）が例外になったときは、違反の判定はそのままで、候補の 1 位の条文と 1 段目の理由を使う（`violation_detector.py:317-332`）
  - タイムアウトは例外として扱われる前に Lambda の上限（30 秒）に当たることがある（課題 ISS-02）
- 重大度: 確信度 0.8 以上 `high`、0.5 以上 `medium`、それ未満 `low`。`MIN_SEVERITY_TO_ALERT`（既定 `low`）以上で通知
- `USE_MOCK_OPENAI=true` のときは「違反」を含む投稿を違反とする（本番は `false`）

### 2.4 記録と通知（`inspection_flow.py`）

新規投稿で違反のとき、次を順に行う（すべて Slack への応答の前）。

1. 投稿者名・チャンネル名・ワークスペース名を取得（失敗しても ID で続ける）
2. `Message_TS` で違反 DB を検索。あれば終了（重複）。検索の失敗は「重複なし」として続ける
3. 投稿のパーマリンクを取得
4. 違反 DB にレコードを作る（`対応ステータス` = `未対応`、`投稿内容` は先頭 500 文字）。失敗すると `None` を返して続ける
5. 条文マスタで条文 ID を探し、見つかれば `対象条文` のリレーションを付け、表示名を取る
6. 管理者チャンネル（SSM の `alert_channel_id`）に通知。ボタンは「警告を送る」「対応不要」と、警告文のテンプレートの選択
7. 通知の `ts` を `通知チャンネルID`・`通知メッセージTS` に保存

違反かどうかにかかわらず、ハンドラーが例外なく終わると、最後にヘルスチェック DB に「正常」を書く（`app_inspect/handler.py:171`）。例外のときは「エラー」を書く。

### 2.5 編集（`violation_transition.py`）

「違反」は、違反と判定され、かつ重大度が `MIN_SEVERITY_TO_ALERT` 以上のもの。違反と判定されても重大度が下回れば「違反でない」と同じではなく、何もしない（`no_action`）。

| 既存のレコード | 編集後の判定 | 動作 |
|---|---|---|
| なし | 違反 | 新規と同じ（2.4） |
| なし | 違反でない | 何もしない |
| あり（状態を問わない。`対応終了`・`対応不要` も含む） | 違反 | 管理者チャンネルの元の通知のスレッドに「投稿が編集されました」。状態は変えない |
| あり | 違反と判定されたが重大度がしきい値未満 | 何もしない |
| `警告済み`・`期限超過`・`再警告済み` | 違反でない | `対応終了` にし、スレッドに通知 |
| それ以外（`未対応`・`対応不要`・`対応終了`） | 違反でない | 何もしない |

スレッドへの通知は、レコードに `通知チャンネルID`・`通知メッセージTS` があるときだけ送る（無ければ送らずに終わる）。

### 2.6 管理者の操作（Lambda B）

| ボタン | 出るところ | 動作 | 新しい状態 |
|---|---|---|---|
| 警告を送る（`approve_violation`） | 最初の通知 | 元の投稿のスレッドに警告文（テンプレート → 無ければ固定文）。`警告送信日時`・`初回警告対応者`・`初回警告テンプレート` を記録 | `警告済み` |
| 対応不要（`dismiss_violation`） | 最初の通知 | 記録だけ | `対応不要` |
| 再警告（`rewarn_violation`） | 48 時間経過の通知 | 元の投稿のスレッドに再警告文。`再警告送信日時` など | `再警告済み` |
| 対応終了（`close_violation`） | 48 時間経過の通知 | 記録だけ | `対応終了` |

- 押した人の権限は確かめない（管理者チャンネルに入っている人なら誰でも押せる）
- 今の状態は確かめない。処理の最後に通知を書き換えてボタンを消す
- Notion の更新が失敗しても（`False` が返っても）成功として扱う

### 2.7 定期処理（Lambda D、5 分ごと）

1. `警告済み` を全件取り、それぞれ: 元の投稿が無ければ `対応終了` ＋スレッドに「削除されました」。`警告送信日時` から 48 時間（`REMINDER_HOURS_THRESHOLD`）たっていれば `期限超過` ＋スレッドに「再警告」「対応終了」のボタン
2. `警告済み`・`期限超過`・`再警告済み` を全件取り、元の投稿が無ければ `対応終了` ＋スレッドに通知

- 投稿があるかは `conversations.history`（`oldest` = `latest` = 投稿の `ts`）で調べる。**API がエラーを返したときも「投稿が無い」と扱う**（`check_message_exists` の except で `False`）【確認済み】
- Slack のトークンはレコードの `team_id` から SSM で引く
- `警告済み` のレコードは 1 回の実行で 2 回調べる
- `未対応` のまま放置されたレコード、`再警告済み` のその後には何もしない

### 2.8 一括判定（Lambda E）

- 入力: `team_id`・`channel_id`・`oldest`・`latest`・`dry_run`。1 回あたり最大 2,000 件
- `run_moderation` を呼ぶが、ローカルルールは渡さない。`OPENAI_MODEL` は環境変数に無く、既定の `gpt-4o-mini` になる → `auto` で `llm_score`【確認済み】
- 違反は Notion に記録するだけで、管理者チャンネルには通知しない。「検出方法」は `バッチLLM`
- スタッフの除外はしない

### 2.9 インストール（Lambda C・F）

- `/slack/oauth/start?team=T…&channel=C…` → Slack の認可 → `/callback` で `bot_token`（と、`channel` を付けたときだけ `alert_channel_id`）を SSM に保存し、テスト通知を送る
- 許可リスト（SSM `/slack/oauth/allowed_team_ids`）が空なら、どのワークスペースでもインストールできる
- 許可リストへの追加は Lambda F（IAM 認証）。GAS は専用の IAM ユーザーのアクセスキーで呼ぶ

## 3. 外部サービスと失敗時の動き

| 呼び出し | 再試行 | 失敗したとき |
|---|---|---|
| OpenAI Decisions API（`requests`） | なし。10 秒 | 予備経路へ |
| OpenAI Chat Completions（SDK） | 2 回（SDK の既定）。読み取り 600 秒 | 違反かどうかを決める段なら「違反なし」。2 段目（条文の選択）なら候補の 1 位の条文で続ける |
| Notion（`requests`、すべて） | なし。5〜10 秒。429 の扱いなし（Notion は 1 接続 1 分 180 回、平均 3 回/秒。超えると 429 と `Retry-After`） | 多くは `None`・`False`・空リストを返して続ける。`_query` だけは例外 |
| Slack Web API（`slack_sdk`） | 接続エラーだけ再試行（SDK の既定）。429 は再試行しない | 呼び出し側による（2.2・2.7） |
| SSM | なし。プロセス内でキャッシュ | 空文字を返す → 設定不足として例外 |

全 Lambda は、例外を最上位で捕まえて HTTP 200 を返す（Slack の再送を防ぐため。`docs/regression_checklist.md`）。

## 4. データ（Notion）

コードが読み書きするプロパティ。Notion の実物との照合は【要確認】。

| DB | 環境変数 | 主なプロパティ |
|---|---|---|
| 違反 | `NOTION_DB_ID` | `投稿内容`（タイトル、500 文字）、`投稿者`、`チャンネル`、`ワークスペース`、`team_id`、`Message_TS`、`投稿リンク`、`検出日時`、`判定結果`、`検出方法`、`違反理由`、`重大度`、`違反カテゴリ`、`信頼度`、`対象条文`、`対応ステータス`、`警告送信日時`、`初回警告対応者`、`初回警告テンプレート`、`再警告送信日時`、`再警告対応者`、`再警告テンプレート`、`通知チャンネルID`、`通知メッセージTS` |
| 条文マスタ | `NOTION_ARTICLES_DB_ID` | `条文ID`（タイトル）、`条項番号`、`内容`、`カテゴリ`、`規約`、`有効`、`workspace`（Relation） |
| ワークスペース一覧 | `NOTION_WS_LIST_DB_ID` | `team_id` |
| テンプレート | `NOTION_TEMPLATE_DB_ID` | `テンプレート名`（タイトル）、`用途`（`警告`・`再警告`）、`本文`、`有効`（true のものだけ使う）。プロセス内で 30 分キャッシュし、取得に失敗したら古いキャッシュ、それも無ければ固定文 |
| ヘルスチェック | `NOTION_HEALTH_DB_ID` | `Lambda名`、`ステータス`、`最終実行日時`、`エラー内容`（Lambda ごとに 1 行を上書き） |

`対応ステータス` の遷移:

| 今の状態 | きっかけ | 次の状態 | 処理する Lambda |
|---|---|---|---|
| （なし） | 違反と判定 | `未対応` | A・E |
| `未対応` | 「警告を送る」 | `警告済み` | B |
| `未対応` | 「対応不要」 | `対応不要` | B |
| `警告済み` | `警告送信日時` から 48 時間 | `期限超過` | D |
| `期限超過` | 「再警告」 | `再警告済み` | B |
| `期限超過` | 「対応終了」 | `対応終了` | B |
| `警告済み`・`期限超過`・`再警告済み` | 投稿が見つからない（API のエラーも含む。ISS-04） | `対応終了` | D |
| `警告済み`・`期限超過`・`再警告済み` | 違反でない内容への編集 | `対応終了` | A |

- ボタンの処理は今の状態を確かめない。ボタンが消えるのは、そのボタン自身を押して処理が最後まで進んだときだけ。`期限超過` で 48 時間経過の通知（「再警告」「対応終了」）が出たあと、違反でない編集（A）や投稿の削除（D）で `対応終了` になっても、その通知のボタンは残る（A・D はスレッドに返信するだけ）。残った「再警告」を押すと、`対応終了` のレコードが `再警告済み` に戻り、元の投稿のスレッドに再警告を送ろうとする
- `未対応`・`対応不要`・`対応終了` から自動で動く遷移は無い

## 5. 設定

| 種類 | 出どころ | 例 |
|---|---|---|
| 秘密情報 | SSM（名前は CDK パラメータ） | 署名シークレット、OpenAI・Notion のキー、OAuth のクライアント情報 |
| ワークスペースごと | SSM `<prefix>/<team_id>/bot_token`・`alert_channel_id` | OAuth で保存 |
| 動作の設定 | CDK パラメータ → 環境変数 | `OPENAI_MODEL`（既定 `gpt-4o-mini`、本番は GitHub の変数）、`DETECTION_MODE`（既定 `auto`）、`MIN_SEVERITY_TO_ALERT`、`REMINDER_HOURS_THRESHOLD`、`RemindScheduleMinutes` |
| コードに直接 | `violation_detector.py` | Decisions API の URL・モデル（`gpt-6-luna`）・タイムアウト（10 秒）・しきい値（0.5）・候補数（3） |

デプロイは GitHub Actions の `deploy.yml`（手動）。テストは CI で Python 3.13（Lambda の実行環境は 3.12）。
