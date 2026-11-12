# Claude と Gemini の役割分担

このプロジェクト (lecture_slack_inspection 配下: `lambda/`、`infra/`、`tests/`、`contracts/`) では、
**調査・計画・レビューは Claude、実装は Gemini** が担当する。
Gemini は Antigravity CLI (`agy`) を、ラッパー `.claude/tools/ask_gemini.sh` 経由でのみ呼び出す。

## 役割

| 担当 | 内容 |
|---|---|
| Claude | 調査 (既存コード・仕様・Slack / Notion / OpenAI の API を読む)、計画、Gemini への指示書の作成、Gemini が書いたコードのレビュー (敵対的な観点を含む)、テストの実行、ユーザーへの報告 |
| Gemini | コードの作成・変更 (`edit` モード)。設計についての意見出しや、ユーザーが求めたときの追加の敵対レビュー (`plan` モード) |

- コードの作成・変更は Gemini に依頼する。Claude が直接コードを書くのは「例外」に挙げた場合だけ。
  Edit / Write でコードファイル、または `lambda/`・`contracts/` 配下の .md 以外のファイル
  (判定プロンプト `judge_violation.txt`、`judge_violation.response_format.json`、`common/data/articles.json`、フィクスチャなど) を
  直接書き換えようとすると、フック (`delegate_impl_to_gemini.py`) がユーザーに確認を求める。
  Bash (sed -i、ヒアドキュメント、スクリプト) での書き換えも同じ扱いにする。
- レビューは Claude が行う。Gemini に plan モードでレビューさせた場合も、その指摘は Claude がコードと実行結果で裏を取ってから採否を決める。
- レビューで見つかった不具合は、原則として Gemini に修正を依頼する。

## 手順

1. **調査**: 関係するファイル・仕様を Claude が読み、何を変えるべきかを確定する。
   違反検知を触るときは `lambda/app_inspect/services/violation_detector.py` のモジュール docstring を先に読む
   (RAG と NG ワードの高速判定は削除済み。再導入しない)。Notion のプロパティ名は `docs/notion_db_schema.md` で確かめる。
2. **計画**: 変更内容・対象ファイル・受け入れ条件を決める。複数ファイルにまたがる変更や設計判断を含む場合は、
   Gemini に渡す前にユーザーに計画を示す (新機能は `clarify-feature` スキルに従う)。
3. **指示書**: `.claude/pending/` に指示ファイルを書く (`.claude/pending/_template.md` をコピーする)。
4. **実行**: `/home/hiroto/DX_PJT/lecture_slack_inspection/.claude/tools/ask_gemini.sh edit <指示ファイルの絶対パス>` を **`run_in_background` で** 実行する。
   バックグラウンド実行はシェルの作業ディレクトリがこのプロジェクトとは限らないので、ラッパーも指示ファイルも**絶対パス**で渡す
   (AIE で相対パスで呼んで「No such file or directory」(終了コード 127) になったことがある)。
   実装の依頼は時間がかかるので、必要なら `GEMINI_TIMEOUT=1800` を前に付ける (デフォルト 900 秒)。
   モデルは `GEMINI_MODEL="Gemini 3.8 Flash (High)"` のように agy の表示名で指定できる (2026-10-13 時点で使える Gemini はこの Flash の High/Medium/Low。`pro` は使えない)。
   敵対レビューなど精度が要る依頼は High を使う。
   数分かかり、`agy` は最後にまとめて出力する。完了通知が来るまで結果を判断しない。
5. **検証**:
   - ラッパーが出す「変更されたファイル」を見て、指示外のファイルが変わっていないか確認する。
   - テストを Claude が実行する (CI の `.github/workflows/test_lambda.yml` と同じく Python 3.13 で `pytest tests/`)。
     `.venv` は別用途 (pyproject.toml の実験用) で pytest が入っていないので、uv の一時環境で実行する
     (リポジトリにファイルを作らない。依存はルートの `requirements.txt` に pytest ごと入っている):
     ```bash
     # lecture_slack_inspection/ で
     uv run --no-project --python 3.13 --with-requirements requirements.txt python -m pytest tests/ -q
     uv run --no-project --python 3.13 --with-requirements requirements.txt python -m pytest tests/unit/test_app_inspect.py -q
     ```
     2026-11-12 時点で 170 件すべて通る（うち `tests/repro/` の再現テストが 41 件。最新は `claude/memo` の `handover/`）。
   - 課題の再現・現状の動きの裏取りに使ったテストは `tests/repro/` に残す。根拠の残し方は `.claude/rules/evidence.md`
   - `infra/` を変えたら `cdk synth` で CloudFormation を生成できるか確かめる (本番は変えない。出力の `cdk.out/` は gitignore 済み):
     ```bash
     # lecture_slack_inspection/infra/ で
     uv run --no-project --python 3.13 --with-requirements requirements.txt npx --yes aws-cdk@2 synth --quiet
     ```
     `infra/tests/` は今の構成 (アプリの Lambda 6 個、ワークスペースごとの SSM) に追従しておらず 3 件とも失敗する (CI でも実行していない)。
     合否の判定には使わない。
   - コードを読んでレビューする: 仕様どおりか、境界条件、例外の扱い、障害時の挙動、既存の呼び出し側との互換性、テストの抜け。
     このプロジェクトで特に見る点:
     - Slack の 3 秒ルールとリトライ (`X-Slack-Retry-Num`) で、同じ投稿を二重に処理・通知しないか (`Message_TS` での重複排除)
     - 編集イベントの状態遷移 (`violation_transition.py`) と `対応ステータス` のライフサイクルを壊していないか
     - `NotionClient` を 3 つの DB ID (`db_id`、`articles_db_id`、`ws_list_db_id`) で作っているか (欠けるとワークスペースの絞り込みが黙って無効になる)
     - ワークスペースごとのトークン (`load_config(team_id)`) を取り違えていないか、OpenAI のモデル名をハードコードしていないか
     - 全 Lambda は Slack のリトライ嵐を防ぐため例外を握りつぶして HTTP 200 を返す設計 (`docs/regression_checklist.md`)。
       Notion / OpenAI / Slack の API が失敗したときに、黙って止まらず、ログ (`common/observability.py`) に残しているか
6. **修正**: 問題があれば、具体的な不具合と期待する挙動を書いた修正指示を Gemini に出し、手順 4〜5 を繰り返す。
7. **報告**: Gemini が作ったもの、Claude が見つけた問題と対応、テスト結果をユーザーに報告する。
   main 以外の作業ブランチなら、Claude は区切りごとに commit してよい (main では commit しない。2026-11-12 にユーザーが許可)。作業記録 (引継ぎ書・指示書の写し) はメモ用のブランチ `claude/memo` に置く (`.claude/rules/memo-branch.md`)。push・デプロイ・PR マージはしない (フックでも禁止している)。

## 指示書の書き方

このプロジェクトのルール (秘密情報・受講生の投稿データ・git とデプロイの禁止など) はラッパーが先頭に自動で付けるので、指示書には書かなくてよい。
`.claude/pending/_template.md` の項目をすべて埋める。

- **目的**: 何を作る・直すのか
- **読むファイル**: 参考にするファイルと関数名
- **触ってよいファイル**: 作成・変更してよいファイルを列挙し、それ以外は変更しないと明記する
- **要件**: 機能・入出力・エラー時の挙動。曖昧さを残さない
- **受け入れ条件**: Claude が何を実行して合否を判定するか
- **テストの書き方**: 参考にする既存のテストとモックの方法を明記する
  - 外部サービス (Slack の `WebClient`・`SignatureVerifier`、OpenAI、`NotionClient`、`secret_manager`) は
    `tests/conftest.py` の `mock_external_services` フィクスチャでモックする。実際の API は呼ばない
  - ワークスペースごとの設定 (`load_config(team_id)`) は `mock_config` フィクスチャでモックする
  - 環境変数は `conftest.py` の autouse フィクスチャ (`mock_env`) が入れるダミー値を使う
  - Slack のイベントやボタン操作のペイロードは `contracts/fixtures/` のものを使う
- **作業方法**: 次の一文を必ず入れる
  > シェルコマンドは一切実行しないこと。ファイルの読み取りと、指定したファイルの書き込みだけで完結させること。テストの実行はこちらで行う。
- **報告形式**: 作成・変更したファイルと各部分の要約、判断に迷った点

## Gemini の制約と注意

- **シェルコマンドを実行させない**: 非対話モードでは許可を求められず自動で拒否され、作業全体が中断する (終了コード 4)。
- **ラッパーの終了コード**:
  - 0: 成功
  - 2: 使い方の誤り
  - 3: 変更してはいけないファイル (`.claude/` 配下、`.env` 系、`violation_data.json` などの投稿データ) が変わった。すぐにユーザーに報告する
  - 4: Gemini の作業が中断された
  - 5: plan モードなのにファイルが変更された
  - その他: `agy` 自体の失敗 (認証切れなど)。ログを確認する
- ログは `.claude/gemini_logs/` に残る。失敗時はログを確認してから再依頼する。
- `agy` / `gemini` の直接実行は settings とフック (`guard_gemini_direct.py`) で禁止している。回避しない。
  このフックはコマンドの区切りの直後にある `agy` などの語を見るので、ヒアドキュメントの中に書いても止まる。
  その場合は Write でファイルに書いてから使う。
- 旧 Gemini CLI (`gemini`) は個人向けの無料ログインが使えなくなった (IneligibleTierError) ため使わない。

## Gemini に渡してはいけないもの

ラッパーの前置きとは別に、Claude 側でも次を守る。指示書に書き写さない・参照させない。

- 秘密情報: `.env` 系、認証情報、Slack のトークンと署名シークレット、OpenAI / Notion の API キー、SSM パラメータの値
- 受講生の Slack 投稿と違反記録: `violation_data.json` (Notion の違反 DB の書き出し)、`output.json`・`team_logs.json` (CloudWatch Logs の書き出し)、
  `output.csv`、`evals/promptfoo/datasets/`。不具合の再現に投稿が必要なときは、架空の文面を作って渡す
- 本番の設定の値: Notion の DB ID、ワークスペースの `team_id`、通知チャンネル ID、GitHub Actions の secrets / vars の値

## 本番環境

この Lambda は複数の受講生ワークスペースで動いている。Claude もユーザーの了承なしに次をしない。

- デプロイ: `cdk deploy`、GitHub Actions の Manual Deploy (`gh workflow run`)、Lambda のコード・設定の更新 (フックで禁止している)
- 本番の状態を変える AWS 操作: SSM パラメータの書き換え、Lambda の手動実行 (`aws lambda invoke`)、EventBridge のルールの変更 (フックで禁止している)
- 本番の Notion DB (違反 DB・条文マスタ・ワークスペース一覧) への書き込み (Notion MCP の create / update を含む) と、Slack への投稿

`cdk synth` と `cdk diff` は本番を変えないので、`infra/` の変更の確認に使ってよい。

## 例外 (Claude が直接書いてよい場合)

- 指示書・設定ファイル・ドキュメント (`.claude/` 配下、`docs/`、`*.md` など) と、学習用の教材・課題 (`learning/` 配下) の作成・更新
- 誤字や 1〜2 行で済む自明な修正で、Gemini に往復させるほうが非効率な場合
- 同じ修正を Gemini に 2 回依頼しても直らなかった場合
- Gemini が使えない (認証切れなど) ときに、ユーザーが Claude の実装を了承した場合

いずれの場合も、Claude が直接変更した箇所と理由をユーザーへの報告に含める。
