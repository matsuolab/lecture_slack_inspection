# 課題の一覧（現状の不足点）

- 作成日: 2026-11-11
- 版: 第 2 版（Claude の再現の確認と、Gemini 3.8 Flash (High) の敵対レビューを反映）
- 基準: コミット `cae84fc`（行番号はすべてこのコミットで数えた。`python3 docs/requirements/evidence/check_line_refs.py cae84fc <文書>` で照合できる。作業ツリーの `moderation.py`・`slack_notifier.py` は空行 1 行の分だけずれている）。現状の動きは [現状の仕様](current_system.md)、既存ドキュメントとの食い違いは 照合の記録（内部資料。リポジトリには置いていない）
- 優先の基準: **判定漏れ（警告すべき投稿の見逃し）を出さないこと**を最優先にする（運営の方針）。次に、誤った対応（誤って対応終了にする・二重に警告する）、運用で気づけないこと、の順

## 区分

| 項目 | 値 |
|---|---|
| 重大度 | **高**: 判定漏れ・誤った対応が、黙って起きる／**中**: 条件付きで起きる、または気づける／**低**: 保守性・将来のリスク |
| 確度 | **再現済み**: 本物のコードを呼んで（外部サービスはモック）現象を再現した／**確認済み**: コード・設定・公式の資料を読んで確かめた（実行はしていない）／**要再現**: コードからは強く疑われるが、Slack などの実際の挙動を検証用ワークスペースで確かめる必要がある／**要確認**: 外部の条件（契約・設定・Notion の実物）次第 |

再現の方法: `lambda/` を import し、Slack・OpenAI・Notion・SSM をモックにして、該当の関数を呼んだ。第 1 版の再現のスクリプトは scratchpad に置いたため消えた。2026-11-12 に、同じ再現を `tests/repro/test_iss<番号>_*.py` の特性テスト（今の不具合のある動きを assert する。直したら期待値を書き換える）として作り直し、**テストが通ったものだけを「再現済み」とした**。各課題の「裏取り」にテストのファイルを書いた。

## 一覧

| ID | 課題 | 重大度 | 確度 | 分類 |
|---|---|---|---|---|
| ISS-01 | 違反かどうかを決める段の失敗が「違反なし」になり、記録・通知・エラーのログのどれも残らない | 高 | 再現済み | 判定漏れ |
| ISS-02 | Chat Completions にタイムアウトの指定が無く、Lambda の 30 秒を超えると投稿ごと失われる | 高 | 確認済み（発生頻度は未測定） | 判定漏れ |
| ISS-03 | Slack からの投稿の通知に、判定を全部済ませてから返事をしており、3 秒の約束を守れていない。送り直しは捨てるので、失敗した投稿をやり直す手段が無い | 中 | 確認済み（ログで実測） | 判定漏れ・信頼性 |
| ISS-04 | 投稿の存在確認が Slack API のエラーを「削除された」と扱い、誤って対応終了にする | 高 | 再現済み | 誤った対応 |
| ISS-05 | スレッドの返信への警告が、5 分以内に「削除された」と誤判定されるおそれ | 高 | 要再現 | 誤った対応 |
| ISS-06 | Slack のレート制限の変更（非 Marketplace アプリ）に当たると、ISS-04 と重なって大量に誤って対応終了になる | 高 | 要確認 | 誤った対応 |
| ISS-07 | `app_batch` は移行後も旧方式（gpt-4o-mini）のまま。廃止後は全件「違反なし」 | 高 | 再現済み | 判定漏れ |
| ISS-08 | `subtype` 付きの投稿（ファイル付きの投稿など）を判定していない | 高 | 再現済み（コード）／要再現（Slack が付ける `subtype`） | 判定漏れ |
| ISS-09 | スタッフの除外を `real_name` の「松尾研」で行う。`users.info` が失敗すると投稿ごと判定されない | 中 | 再現済み（`real_name` を本人が変えられるかは要確認） | 判定漏れ・セキュリティ |
| ISS-10 | 許可リストが空だと、どのワークスペースでもインストールできる | 中 | 再現済み | セキュリティ |
| ISS-11 | `channel` を付けずにインストールすると、そのワークスペースの投稿がすべて判定されない | 中 | 再現済み | 判定漏れ |
| ISS-12 | Notion への記録・更新の失敗を無視して続ける。警告したのに状態が `未対応` のまま、などが起きる | 中 | 再現済み | 誤った対応 |
| ISS-13 | 重複の確認が失敗すると「重複なし」になる。`Message_TS` はチャンネルをまたいで一意でない | 低 | 再現済み | 誤った対応 |
| ISS-14 | ボタンの二重押しで警告が二重に送られる。押した人の権限を確かめない。終わったレコードのボタンが残る | 中 | 再現済み | 誤った対応・セキュリティ |
| ISS-15 | 失敗に気づく仕組みが弱い（アラーム・DLQ が無い、ヘルスチェックは上書き、判定の失敗はメトリクスにならない） | 高 | 再現済み | 運用 |
| ISS-16 | Notion のレート制限への対策が無く、定期処理は 5 分ごとに全件を読み直す | 中 | 確認済み（上限に当たるかは未測定） | 性能 |
| ISS-17 | 状態の流れに抜けがある（`未対応` の放置、`再警告済み` のその後、対応終了の投稿が再び違反になったとき） | 中 | 再現済み | 業務 |
| ISS-18 | 条文の正本が 2 か所に分かれ、ローカルルールが本番で使われていない可能性がある | 中 | 一部要確認 | データ |
| ISS-19 | 設定のずれ（Decisions API のモデル名を直接書いている、CDK の既定値が gpt-4o-mini、Python の版が環境ごとに違う） | 低 | 確認済み | 保守 |
| ISS-20 | GAS 用の IAM アクセスキーのシークレットを取り出せない | 中 | 確認済み（AWS の資料） | 運用・セキュリティ |
| ISS-21 | テストが欠けている（`infra/tests` は古い。この一覧の組み合わせを確かめるテストが無い） | 低 | 確認済み | 品質 |
| ISS-22 | 作業ツリーに、壊れたプロンプト（`judge_violation.txt`）の変更がある | 低 | 確認済み | 保守 |
| ISS-23 | ボタンの value に判定理由を全文入れている。**実際の経路では理由は 800 文字で切られるので、理由だけでは 2,000 文字の上限を超えない**（2026-11-12 に訂正）。防いでいるのは切り詰めだけ | 低 | 再現済み（訂正後の内容） | 保守 |
| ISS-24 | 非公開チャンネル・DM を監視していない。それが仕様かどうかが決まっていない | 中 | 要確認 | 範囲 |
| ISS-25 | ドキュメントがコードと食い違っている | 中 | 確認済み | 保守 |
| ISS-26 | 編集イベントではスタッフの除外が効かない | 低 | 再現済み | 誤検知 |
| ISS-27 | `team_id` の無いレコードを、定期処理が黙って永久にスキップする | 中 | 再現済み（該当の件数は要確認） | 誤った対応 |
| ISS-28 | ローカルルールの取得がページ送りをせず、1 回の応答の分しか読まない | 低 | 再現済み | 判定漏れ |
| ISS-29 | プロンプトインジェクションへの耐性を評価していない | 中 | 要確認 | 判定漏れ・セキュリティ |
| ISS-30 | `app_batch` の `dry_run` で、受講生の投稿本文の先頭 200 文字を CloudWatch Logs に出す | 低 | 確認済み | 個人情報 |
| ISS-31 | OAuth の `state` が使い捨てになっていない | 低 | 確認済み | セキュリティ |
| ISS-32 | Slack の送り直しを、理由（`x-slack-retry-reason`）を見ずにすべて捨てる。最初の 1 回が届かなかった・途中で失敗した投稿は一度も判定されない | 中 | 再現済み（発生頻度は未測定） | 判定漏れ |
| ISS-33 | OpenAI を 1 つの組織・プロジェクト・キーで全ワークスペースから呼び、429（レート制限・使用額の上限）の扱いが無い。Decisions API の 429 で予備経路に切り替わり、呼び出しが増える。上限にかかると黙って「違反なし」 | 中 | 確認済み（コード・公式の資料。上限に届く規模は推計） | 判定漏れ・規模 |
| ISS-34 | Notion を 1 つの接続で全 Lambda から呼び、違反でない投稿 1 件でも 3〜4 回呼ぶ。1 接続 1 分 180 回の上限に、集中した 1 分に約 40 件で届く（推計）。違反 DB は 1 データベース 25 万行が上限。検索の 1 万件の打ち切りを見ない | 中 | 確認済み（コード・公式の資料。届く規模は推計） | 規模・データ |

## 詳細

### ISS-01 違反かどうかを決める段の失敗が「違反なし」になる

- 現象: 次の段の例外は `is_violation=False` になり、Notion にも管理者チャンネルにも何も残らない
  - `llm_score` の判定（`violation_detector.py:262-269`）
  - `luna` の 1 段目、`decisions` で Decisions API が失敗したあとの予備経路（`violation_detector.py:516-523`）
  - `ViolationDetector` そのものの例外（`moderation.py:44-50`。`print` だけ）
- 失敗しても `_moderate` は `InferenceLatencyMs` を出すだけで、`log_error` も失敗のメトリクスも出さない（`app_inspect/handler.py:29-78`）
- 例外ではないもの: 2 段目（条文の選択）の失敗は、違反の判定を保ったまま候補の 1 位の条文を使う（`violation_detector.py:317-332`）。これは判定漏れにならない
- 裏取り: 再現済み。`llm_score`・`luna`・`decisions`（両経路とも失敗）・`run_moderation` の 4 通りで `is_violation=False`。`_moderate` が出すメトリクスは `InferenceLatencyMs` だけ。2 段目の失敗では `is_violation=True`・候補の 1 位（Gemini の指摘で追加）（テスト: `tests/repro/test_iss01_detection_failure_is_non_violation.py`）
- 影響: 判定漏れが黙って起きる。過去に、`temperature=0` を受け付けないモデルで全投稿が「違反なし」になる組み合わせがあった（移行の報告書 9 章 5）
- 対応の方向: 「判定できなかった」を、違反・非違反とは別の結果として扱い、記録・通知・再試行のどれかにつなげる（要件で決める）

### ISS-02 Chat Completions にタイムアウトの指定が無い

- 現象: `OpenAI(api_key=...)` をタイムアウトの指定なしで作る（`app_inspect/handler.py:67`）。openai SDK の既定は読み取り 600 秒・再試行 2 回。`chat.completions.create` の 3 か所（`violation_detector.py:244-248, 305-309, 468-472`）にもタイムアウトの指定が無い。Decisions API だけが 10 秒（`violation_detector.py:21, 367-375`）
- 影響: OpenAI の応答が遅いと、Lambda の上限（30 秒、`infra_stack.py:193`）で強制終了される。この場合は例外の処理（ISS-01）にも入らず、記録・通知・ヘルスチェックのどれも残らない。Slack の再送は捨てるので、やり直されない
- 裏取り: 確認済み。SDK の既定値は `lambda/requirements.txt` と同じ版（openai 2.20.0）で確かめた。実際にどのくらい起きているかは未測定（2026-08-13 のログ 80 回では、Lambda のタイムアウトは 0 回）

### ISS-03 Slack からの投稿の通知への返事が遅い（3 秒の約束）

- 何の話か: Slack は投稿・編集のたびに `POST /slack/events`（Lambda A）へ通知を送り、3 秒以内の返事を求める。ボットは判定・記録・管理者への通知を全部済ませてから返事をしている。問題点の整理と選択肢は [decisions.md の D2](decisions.md)
- 判定した投稿 32 件の内訳（2026-08-13）: 3 秒を超えたのは 26 件（中央値 約 4.9 秒）。判定しないイベントは 0.4 秒以内、送り直しは 0.02 秒以内に捨てている
- 既存の計画書: `docs/specs/20261013_slack-3s-ack.md`（案 A: 非同期の自己呼び出しを推奨、未決）
- 公式の資料で確認した仕様（https://docs.slack.dev/apis/events-api/）: 3 秒以内に 2xx。再送は 3 回（すぐ・1 分後・5 分後）。60 分間で 95% を超えて失敗すると購読を一時停止。ただし 1 時間 1,000 件未満のアプリは対象外
- 実測（2026-08-13 のログ、約 7 時間）: 実行 80 回のうち 26 回が 3 秒を超えた（p90 約 5.6 秒、最大 7.8 秒）。判定した 32 件のうち 30 件で 1 回目の再送が来た（すべて捨てた）
- **ログの数値についての注意（2026-11-12）:** この節の数値は 1 日分（約 7 時間）のログで、判定方式の移行の前（`llm_score`、OpenAI の呼び出し 1 回）のもの。集計のスクリプトは残っていない。数値は運営が別途確かめる。p90 はこの節では約 5.6 秒、照合の記録の S6 では 5.7 秒と食い違っている。コードから言えること（ログに頼らない）は [decisions.md の D2](decisions.md) の「コードから言えること」にまとめた
- 3 秒を超えると何が起きるか（コードとログで確認）:
  - Slack が同じ通知を送り直す。ボットは送り直しを、署名の確認より前にすべて 200 で捨てる（`app_inspect/handler.py:89-95`）ので、二重の判定・通知は起きない
  - Slack が待つのをやめても、Lambda は最後まで動く（判定・記録・通知は行われる）
  - Slack に通知を止められることは、実質的に無い。止める条件は「60 分間で 95% を超えて失敗」で、1 時間 1,000 件未満のアプリは対象外（8 月は送り直しを含めて 1 時間に約 11 件）。1,000 件を超えても、送り直しには 3 秒以内に返事をする（起動直後でも最大 1.96 秒）ので、失敗率は 50% 前後にとどまる（8 月は 80 回中 26 回、約 33%）
  - 判定の部分（`InferenceLatencyMs`。Notion へのワークスペースの問い合わせを含む）は中央値 約 2.8 秒。新しい判定方式でも、起動の時間（1.2〜2.0 秒）と Notion・Slack の呼び出しは残るので、超え続ける見込み
- **結論: 3 秒の約束を破ること自体は、今の作りでは実害を生んでいない。** 害があるのは「最初の 1 回が失敗した投稿を、送り直しで救えない」こと（ISS-01・02・32 と同じ根）
- 「先に返事をして、あとで判定する」作りにはなっていない（`app_inspect/handler.py:151-172` で判定・記録・通知を待ってから返す）。Slack から投稿を読み直す処理も無い（送り直しは Slack が自分から送ってくるもの）

### ISS-04 存在確認のエラーを「削除された」と扱う

- 現象: `check_message_exists` は `SlackApiError` をすべて `False`（投稿が無い）にする（`app_remind/services/reminder.py:23-42`）。呼び出し側は `False` なら `対応終了` にし、管理者に「投稿が削除されました」と通知する（`警告済み`: `reminder.py:245-251`、3 つのアクティブな状態: `reminder.py:333-341`）
- `slack_sdk` の `WebClient` は既定で 429 を再試行しない（`retry_handlers` は接続エラーだけ）
- 裏取り: 再現済み。`ratelimited`・`not_in_channel`・`account_inactive`・`channel_not_found` のすべてで `False`。`process_deletion_check` と `process_reminders` の両方で `mark_closed` と「削除されました」の通知が実行された（テスト: `tests/repro/test_iss04_existence_check_error_treated_as_deleted.py`）
- 影響: 警告中の違反が、削除されていないのに追跡から外れる。管理者は削除されたと信じる

### ISS-05 スレッドの返信を「削除された」と誤判定するおそれ

- 現象: スレッドの返信（`thread_ts` があり `subtype` が無い）は新規投稿として判定される。警告後の存在確認は、パーマリンクから返信自身の `ts` を取り出し、`conversations.history` を `oldest = latest = 返信の ts` で呼ぶ。`conversations.replies` は使わない
- 裏取り: コードの側は確認済み（2026-11-12 に「再現済み」から変えた。再現のテストを作り直していないため）。返信のパーマリンクから返信の `ts` を取り出し（`notion_client.py:385-397`）、`conversations.history` だけを呼ぶ（`app_remind/services/reminder.py:23-42`）。親だけが返るとすると「存在しない」になる。Slack の公式の資料は、返信の取得には `conversations.replies` を使うよう案内しているが、`conversations.history` が返信を返さないとは明記していない。そのため確度は「要再現」のままにする
- 予想される影響: スレッドの返信への警告は、次の定期処理（5 分以内）で `対応終了` ＋「削除されました」になる
- 確かめ方: 検証用ワークスペースで、スレッドの返信に違反を書き、警告を送って 5 分待つ

### ISS-06 Slack のレート制限の変更

- 公式の告知（https://docs.slack.dev/changelog/2025/05/29/rate-limit-changes-for-non-marketplace-apps）: Marketplace に載っていない、商用に配布されたアプリは、`conversations.history`・`conversations.replies` が 1 分に 1 回・1 回 15 件までになる。2025-05-29 以降に作ったアプリと新しいインストールにすぐ適用。社内向けに顧客が作ったアプリは対象外（定義は明記されていない）
- このアプリが対象になるかは、配布の形（松尾研の講座のワークスペースへ OAuth で配布）次第で、未確認
- 対象なら: 定期処理（5 分ごとに `警告済み` 1 件あたり 2 回）がすぐ上限に当たり、ISS-04 と重なって、警告中のレコードがまとめて対応終了になる。`app_batch` もほぼ使えない
- 確かめ方: Slack のアプリの管理画面で区分を確認する。CloudWatch Logs（`app_remind`）で `ratelimited` を検索する

### ISS-07 `app_batch` は旧方式のまま

- 現象: Lambda E の環境変数に `OPENAI_MODEL` が無い（`DETECTION_MODE` はある。`infra_stack.py:326-336`）。既定の `gpt-4o-mini`（`app_batch/services/config.py:36`）→ `auto` で `llm_score` になる。移行の手順（GitHub の変数を変える）では変わらない
- その他: ローカルルールを渡さない、スタッフを除外しない、管理者に通知しない、「検出方法」を `バッチLLM` で上書きする（`app_batch/handler.py:105-134`）。予備経路に入ると 1 回の実行（最大 2,000 件、15 分）が時間切れになりうる
- 裏取り: 再現済み。`OPENAI_MODEL` を外した環境で `load_config` → `ViolationDetector` が `gpt-4o-mini`・`llm_score` になった（テスト: `tests/repro/test_iss07_batch_uses_old_model.py`）

### ISS-08 `subtype` 付きの投稿を判定していない

- 現象: `subtype` が無い投稿と `message_changed` 以外はすべて捨てる（`slack_event_parser.py:35-108`）
- 裏取り: コードの側は再現済み。`file_share`・`thread_broadcast`・`me_message` の投稿は、本文に勧誘の文があっても `None`（判定しない）になった（テスト: `tests/repro/test_iss08_subtype_messages_ignored.py`）
- 不確かな点: 今の Slack が、ファイルを添えた投稿やチャンネルにも送った返信に、実際にどの `subtype` を付けて送るか（公式の資料には「`file_share` はもう送られない」との記載もある）。検証用ワークスペースで実際のイベントを見る

### ISS-09 スタッフの除外

- 現象: `real_name` に「松尾研」を含むと判定しない（`app_inspect/handler.py:136-141`）。`real_name` を本人が変えられる設定なら、受講生も同じ名前にすれば素通りする（ワークスペースの設定次第で要確認）
- `users.info` が失敗すると例外になり、全体の except で `error_handled`（200）。その投稿は判定されない
- 裏取り: 再現済み。`real_name` に「松尾研」を含むと `ignored_admin`。`users.info` の例外で `error_handled`、判定の関数は呼ばれなかった（テスト: `tests/repro/test_iss09_staff_exclusion_by_real_name.py`）

### ISS-10 許可リストが空なら誰でもインストールできる

- 現象: 許可リストの判定が `if allowed and …` で、空なら素通りする（`app_oauth/handler.py:202-204, 290-299`）
- 裏取り: 再現済み。許可リストが空で、知らない `team` でも開始は 302、コールバックは保存まで進み 200（テスト: `tests/repro/test_iss10_empty_allowlist_allows_any_team.py`）
- 影響: 想定外のワークスペースからのイベントで、OpenAI・Notion の費用と記録が増える。違反 DB に関係のないワークスペースの投稿が入る

### ISS-11 `alert_channel_id` が無いワークスペース

- 現象: インストール URL に `channel` を付けないと `alert_channel_id` が保存されない（`app_oauth/handler.py:181-191`）。`load_config` が例外を出し（`app_inspect/services/config.py:41-42`）、そのワークスペースのすべてのイベントが `error_handled` で終わる
- 裏取り: 再現済み。`channel` 無しでは `bot_token` と `installed_at` だけが保存された。`alert_channel_id` が無いと `load_config` が `RuntimeError`（テスト: `tests/repro/test_iss11_install_without_channel.py`）
- 気づける手段: ヘルスチェック DB の「エラー」（次の正常な実行で上書きされる。ISS-15）とログだけ

### ISS-12 Notion の失敗を無視して続ける

- 記録の失敗: `create_violation_log` は失敗すると `None` を返す（`notion_client.py:222-232`）。それでも通知を送り、ボタンの value に `notion_page_id` が入らない（`inspection_flow.py:110-155`）。警告しても Notion は変わらず、48 時間の追跡が始まらない。契約のスキーマ（`notion_page_id` 必須）とも食い違う
- 更新の失敗: `update_status` などは失敗すると `False` を返す（`notion_client.py:274-287`）が、ボタンの処理は戻り値を見ずに成功として扱い、ボタンを消す（`app_alert/services/actions.py:157-167, 185-194, 237-246, 265-274`）
- 裏取り: 再現済み。記録が `None` でも通知が送られ、value に `notion_page_id` が無かった。`update_status` が `False` でも、警告のボタンの処理は `True` を返し、`chat_update` を呼んだ（テスト: `tests/repro/test_iss12_notion_failures_ignored.py`）

### ISS-13 重複の確認

- 確認の失敗は「重複なし」（`notion_client.py:140-142`）
- 検索条件は `Message_TS` だけ（`notion_client.py:135-138, 150-153`）。`ts` は同じチャンネルの中でだけ一意。別のチャンネル・ワークスペースで同じ `ts` があると、編集の処理が別の投稿のレコードを拾う（確率は低い）
- 裏取り: 再現済み（テスト: `tests/repro/test_iss13_duplicate_check.py`）

### ISS-14 ボタンの二重押し・権限・残ったボタン

- 状態を確かめずに処理し、最後にボタンを消す。2 人がほぼ同時に押すと警告が 2 回送られる（`app_alert/services/actions.py:122-171`）
- 押した人の権限は確かめない（管理者チャンネルのメンバーなら誰でも）
- `期限超過` の 48 時間経過の通知のボタンは、違反でない編集や削除で `対応終了` になっても消えない。押すと `対応終了` が `再警告済み` に戻る（現状の仕様 4 章）
- 裏取り: 再現済み（同じ value で 2 回呼ぶと、警告が 2 回送られ、Notion を 1 回も読まない）（テスト: `tests/repro/test_iss14_double_click_sends_twice.py`）

### ISS-15 失敗に気づけない

- CloudWatch のアラーム・DLQ が無い（`infra_stack.py` 全体）
- ヘルスチェック DB は Lambda ごとに 1 行を上書きする（`notion_client.py:461-514`）。エラーの直後に正常な実行があると、ステータスとエラーの内容が消える。`app_batch`・`app_oauth`・`app_admin` は書かない
- 判定の失敗（ISS-01）はメトリクスにならない
- 標準の logging の `info`（`logger.info`）は、Lambda の既定のログレベルでは出ない可能性がある（要確認）。`observability.log_info`（`print`）は常に出る
- 裏取り: 再現済み（ヘルスチェックの上書きと、判定の失敗でメトリクスが出ないこと）（テスト: `tests/repro/test_iss01_detection_failure_is_non_violation.py::test_moderation_executor_detection_failure_emits_latency_only_and_no_error_log`、`tests/repro/test_iss15_health_row_overwritten.py`）

### ISS-16 Notion の負荷

- Notion のレート制限（https://developers.notion.com/reference/request-limits）: 1 接続あたり 1 分 180 回（平均 3 回/秒）。窓の中でのバーストは許される。超えると 429（`rate_limited`）と `Retry-After`
- このコードは 429 も `Retry-After` も扱わない。違反 1 件で Notion を 8 回前後、判定した投稿ごとにヘルスチェックで 2 回呼ぶ
- 定期処理は 5 分ごとに、`警告済み` と、アクティブな 3 状態を全件読み、1 件ずつ Slack に問い合わせる（`警告済み` は 2 回）。レコードが増えると 60 秒の上限にも近づく
- 裏取り: 確認済み（コードを読んだ）。上限に当たっているかは未測定

### ISS-17 状態の流れの抜け

- `未対応` のまま放置されたレコードを知らせる仕組みが無い（定期処理の対象は `警告済み`・`期限超過`・`再警告済み` だけ）
- `再警告済み` のあとの期限・対応が無い（削除か、違反でない編集でしか終わらない）
- `対応終了`・`対応不要` の投稿が、編集で再び違反になっても「編集されました」と返信するだけで、状態は変わらない
- 裏取り: 再現済み（`対応終了`・`対応不要`・`未対応` のどれでも、違反への編集は `reply_still_violation`）（テスト: `tests/repro/test_iss17_terminal_status_edit.py`）
- これらが仕様かどうかは運営が決める

### ISS-18 条文の正本が 2 か所

- 判定に使うのは `articles.json`（31 条）とローカルルール。Notion の条文マスタは、ローカルルールと表示名・リレーションにだけ使う
- 条文マスタに無い ID（移行の TODO の D によれば 9 件）を選ぶと、`対象条文` のリレーションが付かず、48 時間後の通知などで条文名が出ない
- 2026-08-13 のログ（1 日分。別途確認中）では、判定 32 件のすべてで `local_rules_skipped`（`reason=no_ws_page_id`）だった。そのワークスペースはワークスペース一覧 DB に見つからず、ローカルルールが一度も使われていない。今も同じかは要確認（`team_id` の登録漏れか、`NOTION_WS_LIST_DB_ID` が空か）

### ISS-19 設定のずれ

- `DECISIONS_MODEL = "gpt-6-luna"` を直接書いている（`violation_detector.py:20`。他は `OPENAI_MODEL`）
- CDK の `OpenAIModel` の既定値が `gpt-4o-mini`（`infra_stack.py:135-141`。GitHub の変数が空だと旧方式になる）
- テストは Python 3.13、Lambda は 3.12、デプロイと `cdk synth` は 3.11

### ISS-20 GAS 用のアクセスキー

- CDK が `CfnAccessKey` でキーを作るが、シークレットは出力していない（`infra_stack.py:429-437, 565-575`）。コメントは「コンソール／CLI で一度だけ確認」とする
- AWS の資料（https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-iam-accesskey.html）によれば、シークレットは作成時にしか取り出せない（CloudFormation では `Fn::GetAtt SecretAccessKey` だけ）。今の構成では GAS に渡す手段が無い。作り直すには `Serial` を増やしてキーを入れ替える
- 長期のアクセスキーで、ローテーションの手順も無い

### ISS-21 テストの不足

- `infra/tests` は今の構成に追従しておらず、CI でも動かしていない
- この一覧の「再現済み」の組み合わせを確かめるテストが無かった（今のテストは 129 件すべて通る）→ 2026-11-12 に `tests/repro/` に再現のテスト 41 件を追加した（全体で 170 件すべて通る）。これらは今の不具合のある動きを assert する特性テストなので、直したら期待値を書き換える

### ISS-22 壊れたプロンプトの変更

- 作業ツリーの `judge_violation.txt` の 1〜2 行目の文が入れ替わって壊れている。誤って commit すると、ロールバックの経路（`llm_score`）の判定が悪くなる

### ISS-23 ボタンの value の長さ

- 現象: value に判定理由（`reason`）をそのまま入れる（`slack_notifier.py:66-73`）。2 段目の理由には長さの制限が無い（`violation_detector.py:288-303` の JSON スキーマに `maxLength` が無い）。ただし `run_moderation` が結果を `normalize_result` に通し、理由を 800 文字で切る（`models.py:51`）。value の JSON は `ensure_ascii=False`（`slack_utils.py:18`）なので、日本語でも 1 文字 1 文字のまま
- **訂正（2026-11-12）:** 第 1 版は「理由が 1,950 文字のとき value は 2,101 文字」を再現済みとしたが、その再現は切り詰めを通らない呼び方（`send_new_violation_alert` に長い理由を直接渡す）で作っていた。実際の経路では理由は 800 文字までなので、理由だけでは 2,000 文字を超えない。超えうるのは、条文名（Notion の条文マスタの名前）など他の欄が極端に長い場合だけ
- 裏取り: 再現済み（テスト: `tests/repro/test_iss23_button_value_length.py`。実際の経路で 1,950 文字の理由 → 800 文字・value は 2,000 文字以下。対照として、切り詰めを通らないと 2,000 文字を超える＝`send_new_violation_alert` 自身には長さの制限が無い）
- 重大度: 低のまま（今は起きない。切り詰めの長さを変えると起きうる）。対応の方向（value に理由を入れない）は decisions.md の B のまま

### ISS-24 監視の範囲

- OAuth のスコープに `groups:history`・`im:history` が無く（`infra_stack.py:72-78`）、非公開チャンネルと DM は監視しない。運営の意図かどうかが決まっていない
- Slack アプリの Event Subscriptions の設定（購読しているイベント）はリポジトリに無く、管理画面でしか分からない

### ISS-25 ドキュメントの食い違い

- 照合の記録のとおり。特に `notion_db_schema.md`（`CLAUDE.md` が正本と紹介）、`regression_checklist.md`（手順が誤っている箇所がある）、`aws_architecture.md`（Lambda の数・パラメータが古い）

### ISS-26 編集イベントではスタッフの除外が効かない

- 現象: スタッフの除外は `event.user` を見る（`app_inspect/handler.py:136`）。編集イベント（`message_changed`）は `event` の直下に `user` が無い（Slack の公式のペイロード例: https://docs.slack.dev/reference/events/message/message_changed ）。`users.info` を呼ばずに判定に進む
- 裏取り: 再現済み。`real_name` が「松尾研」のユーザーの編集イベントで、`users.info` が呼ばれず、編集の処理に進んだ（テスト: `tests/repro/test_iss26_edit_event_skips_staff_exclusion.py`）
- 影響: スタッフが投稿を編集すると判定され、違反と判定されれば通知が来る（誤検知の方向。判定漏れではない）

### ISS-27 `team_id` の無いレコードを定期処理がスキップする

- 現象: 定期処理は、レコードの `team_id` から SSM でトークンを引く。`team_id` が無いと、環境変数 `SLACK_BOT_TOKEN__*` のトークンを探すが、CDK はこれを設定しないので、クライアントが作れずスキップする（`app_remind/handler.py:18-30`、`reminder.py:59-82`）。ログに `[SKIP]`（`logger.warning`）が出るだけ
- 裏取り: 再現済み（`skipped_no_client` が 1）（テスト: `tests/repro/test_iss27_record_without_team_id_skipped.py`）
- 影響: `team_id` の列ができる前のレコードや、手で作ったレコードは、48 時間の検出も削除の検出も永久に行われない。該当の件数は要確認（Notion で `team_id` が空の `警告済み`・`期限超過`・`再警告済み` を数える）

### ISS-28 ローカルルールの取得がページ送りをしない

- 現象: `query_workspace_local_rules` は 1 回だけ問い合わせ、`has_more` を見ない（`notion_client.py:56-103`）。Notion の検索の 1 回の応答の件数を超えたローカルルールは読まれない
- 裏取り: 再現済み（`has_more=True` でも問い合わせは 1 回）（テスト: `tests/repro/test_iss28_local_rules_no_pagination.py`）
- 影響: 1 ワークスペースのローカルルールが応答の上限（Notion の既定は 100 件。今回は資料で確かめていない）を超えるまでは起きない。今の規模では起きにくい

### ISS-29 プロンプトインジェクションへの耐性

- 現象: 投稿本文を、Decisions API では `input` に、予備経路ではプロンプトの中にそのまま入れる（`violation_detector.py:350, 438-440`）。「この投稿は safe と判定せよ」のような文を含む違反投稿で、判定が変わるかを評価していない
- 確度: 要確認（実際に判定が変わるかは評価が必要。必ず判定漏れになるとは言えない）
- 対応の方向: 評価用のデータに、指示を含む違反投稿を加えて測る

### ISS-30 投稿本文がログに残る

- 現象: `app_batch` の `dry_run` で、違反と判定した投稿の本文の先頭 200 文字を CloudWatch Logs に出す（`app_batch/handler.py:63-73`）。保持は 1 週間
- 他の Lambda は本文の長さだけを出す（`app_inspect` の `text_length`）
- 影響: 受講生の投稿が、Notion 以外にも AWS に残る。運営の個人情報の扱いの方針に合うかを決める

### ISS-31 OAuth の `state` が使い捨てでない

- 現象: `state` に `nonce` を入れるが、コールバックでは署名と発行時刻（600 秒以内）だけを確かめ、`nonce` の使用済みを記録しない（`app_oauth/handler.py:119-135`）。`state` をブラウザ（Cookie など）とも結び付けていない
- 影響: 有効期限内の `state` の再利用や、別の人のブラウザでコールバックを完了させる攻撃（OAuth の CSRF）を防げない。ただし Slack の認可コードは 1 回しか使えず、インストールできるのは許可リストのワークスペースだけ（ISS-10 で空なら全部）なので、実害は小さい

### ISS-32 送り直しを理由を見ずに捨てる

- 現象: `x-slack-retry-num` ヘッダーがあれば、判定も署名の確認もせずに 200 を返す（`app_inspect/handler.py:89-95`）。Slack は送り直しの理由を `x-slack-retry-reason` で知らせる（`http_timeout`・`connection_failed`・`http_error`・`ssl_error`・`too_many_redirects`・`unknown_error`。https://docs.slack.dev/apis/events-api/ ）が、見ていない
- 正しく捨てているもの: `http_timeout`。最初の 1 回は Lambda で動いているので、捨ててよい
- 取りこぼすもの: 最初の 1 回がボットに届かなかった、または途中で失敗して 2xx 以外を返した場合（例: API Gateway のエラー、デプロイの失敗で Lambda が起動できない、通信の失敗）。送り直しも捨てるので、その投稿は一度も判定されない
- 裏取り: 再現済み。`x-slack-retry-reason` が `http_timeout`・`connection_failed`・`http_error` のどれでも、判定の関数も署名の確認も呼ばれずに 200 になった。2026-08-13 のログでは、送り直し 30 件のすべてに判定済みの最初の 1 回があり、取りこぼしは見つかっていない。理由はログに残していないので、普段の発生頻度は分からない（テスト: `tests/repro/test_iss32_retry_discarded_regardless_of_reason.py`）
- 対応の方向: D2（返事と判定を分ける）と合わせて決める。最低限、理由をログに残す

### ISS-33 OpenAI のレート制限（投稿の増加）

- 現象: OpenAI のキーは SSM のパラメータ 1 つで、全ワークスペースの `app_inspect` と `app_batch` が共有する（`app_inspect/services/config.py:47`、`app_batch/services/config.py:35`）。Lambda の同時実行数に上限が無い。Decisions API はどんな例外でも予備経路（Chat Completions 2 回）に切り替わる（`violation_detector.py:173-182`）ので、429 の最中に呼び出しが増える。Decisions API の呼び出しは再試行も `Retry-After` の扱いも無い（`violation_detector.py:367-376`）。最後は黙って「違反なし」（ISS-01）
- 公式の資料（2026-11-12）: OpenAI のレート制限は**組織とプロジェクトの単位**で、キーの単位ではない（キーを増やしても上限は増えない）。gpt-6-luna は Build の段階で 5,000 RPM・200 万 TPM。失敗したリクエストも上限に数えられる。使用額の上限に届いても 429（https://developers.openai.com/api/docs/guides/rate-limits 、https://developers.openai.com/api/docs/models/gpt-6-luna ）
- 影響: 平均の投稿数では遠いが（1 投稿約 5,000 トークンの推計で、Build でも 1 分に約 380 件）、集中・他のアプリとの共有・使用額の上限で 429 になると、その間の投稿が黙って判定漏れになる
- 裏取り: 確認済み（コードと公式の資料）。上限に届く規模は推計。詳細と対処の選択肢は 検討の記録（内部資料。リポジトリには置いていない）

### ISS-34 Notion の呼び出しの上限と保存数（投稿の増加）

- 現象: Notion の API キーも 1 つで、全 Lambda が同じ接続を使う（`app_inspect/services/config.py:48`）。違反でない投稿 1 件でも、ワークスペースの検索・ローカルルール・ヘルスチェックの検索と更新で 3〜4 回呼ぶ（`app_inspect/handler.py:52-54, 171`、`notion_client.py:461-514`）。違反ならさらに 6〜7 回。検索のページ送りは `has_more` だけを見て、結果の打ち切り（`request_status`）を見ない（`notion_client.py:119-127`）
- 公式の資料（2026-11-12）: 1 接続あたり 1 分 180 回（Business・Enterprise は 600 回）、別にワークスペースで共有する上限（数値は非公開）。1 回の検索の結果は最大 1 万件。1 データベース 25 万行まで、届くと行を足せない（https://developers.notion.com/reference/request-limits 、https://developers.notion.com/guides/data-apis/query-large-data-sources 、https://www.notion.com/help/optimize-database-load-times-and-performance ）
- 影響: 集中した 1 分に約 40 件の投稿で 1 接続の上限に届く（推計）。OpenAI より先に届く。届いたときの 429 は扱っておらず（ISS-16）、記録が落ちる（ISS-12）。違反 DB の 25 万行は、1 日 1 万件・違反 5% なら約 1.4 年で届く（推計）
- 裏取り: 確認済み（コードと公式の資料）。届く規模は推計。詳細は 検討の記録（内部資料。リポジトリには置いていない）

## 敵対レビューの採否（Gemini 3.8 Flash (High)、2026-11-11）

番号付きの指摘 26 件と総評。Claude がコードの再現・公式の資料で確かめてから採否を決めた（採用 23 件、不採用 3 件と総評の 1 点）。

| # | 指摘 | 採否 | 理由・対応 |
|---|---|---|---|
| 1-1 | 2 段目（条文の選択）の失敗は「違反なし」にならない | 採用 | 再現済み。現状の仕様 2.3・3 章と ISS-01 を直した |
| 1-2 | 編集イベントではスタッフの除外が効かない | 採用（重大度は高 → 低） | 再現済み。ISS-26 を追加。誤検知の方向で、判定漏れではないので低 |
| 1-3 | 編集後の違反が重大度のしきい値未満なら何もしない。通知の情報が無ければスレッドに送らない | 採用 | 確認済み（コード。2026-11-12 に「再現済み」から変えた。再現のテストは作り直していない）: `violation_transition.py:42-51`、`inspection_flow.py:233-237`。現状の仕様 2.5 を直した |
| 1-4 | テンプレート DB のプロパティ名（`テンプレート名`・`有効`） | 採用 | `template_manager.py:35-68` で確認。現状の仕様 4 章を直した |
| 1-5 | 状態の遷移図で `期限超過` からの削除・編集が抜けている | 採用 | `violation_transition.py:8`、`notion_client.py:315-323` で確認。表に作り直した |
| 1-6 | ヘルスチェックの「正常」はハンドラーの最後で、違反のときに限らない | 採用 | `app_inspect/handler.py:171` で確認。現状の仕様 2.4 を直した |
| 2-1〜2-6, 4-1, 4-2 | 行番号の誤り | 採用 | 複数のファイルをまとめて `cat -n` で読み、通し番号のまま書いていた。すべてのファイルで数え直した |
| 2-7 | ISS-05 は公式の仕様から「確認済み」にできる | 不採用 | 公式の資料は返信の取得に `conversations.replies` を案内するだけで、`conversations.history` が返信を返さないとは明記していない。「要再現」のままにした |
| 3-1 | `team_id` の無いレコードがスキップされる | 採用 | 再現済み。ISS-27 を追加 |
| 3-2 | ローカルルールの取得がページ送りしない | 採用（重大度は高 → 低） | 再現済み。ISS-28 を追加。上限を超えるローカルルールは今の規模では考えにくい |
| 3-3 | プロンプトインジェクション | 採用（確度は要確認） | ISS-29 を追加。「確実に判定漏れになる」は根拠が無いので書かない |
| 3-4 | テンプレートのキャッシュがワークスペース・DB で分かれない | 不採用 | 再現はしたが、今の構成ではテンプレート DB は 1 つ（環境変数 1 個）で、混線は起きない |
| 3-5 | 投稿本文がログに残る | 採用（重大度は中 → 低） | ISS-30 を追加。`dry_run` のときだけ |
| 3-6 | OAuth の `state` の再利用 | 採用（重大度は中 → 低） | ISS-31 を追加。認可コードが 1 回しか使えないので実害は小さい |
| 4-3 | `CLAUDE.md` の環境変数の一覧に `DETECTION_MODE` が無い | 採用 | 照合の記録に M6 を追加 |
| 5-1 | ISS-06 は区分の確認が要る | 採用（既に要確認） | 変更なし |
| 5-2 | 「1 時間 1,000 件未満は停止の対象外」は第三者の資料だけ | 不採用 | Slack の公式の資料（https://docs.slack.dev/apis/events-api/）に記載があることを確かめ済み |
| 5-3 | 2,000 文字を超えたときの Slack の挙動は要確認 | 採用 | ISS-23 の確度に反映 |
| 5-4 | Notion のレート制限の出典 | 採用 | 公式の資料で確かめ、ISS-16 に出典と内容（バースト可、429 と `Retry-After`）を書いた |
| 総評 3 | `app_alert` で管理者が別のワークスペースにいた場合に壊れる | 不採用 | 具体的な経路の指摘が無い。`app_alert` はボタンが押されたワークスペースのトークンを使い、通知先は同じワークスペースの `alert_channel_id` なので、今の構成で食い違う経路は見つからなかった |

## 次の段階で決めること（要件定義へ）

決めることと、その判断の材料は [decisions.md](decisions.md) にまとめた。回答はそちらの「回答」の欄に書く。

| # | 決めること | 関係する課題 |
|---|---|---|
| D1 | 判定できなかった投稿をどう扱うか | ISS-01・02 |
| D2 | Slack からの投稿の通知の受け取り方（判定を後回しにして先に返事をするか） | ISS-03・12・32 |
| D3 | どの投稿を監視するか | ISS-08・24 |
| D4 | スタッフ（監視しない人）の見分け方 | ISS-09・26 |
| D5 | 対応の流れの抜けをどう埋めるか | ISS-14・17 |
| D6 | 投稿の削除の見つけ方 | ISS-04・05・06 |
| D7 | 条文（判定の基準）の正本をどこに置くか | ISS-18 |
| D8 | 失敗を誰に・どう知らせるか | ISS-15 |
| D9 | 個人情報（投稿本文）をどこに・どれだけ残すか | ISS-30・24 |
| D10 | 進める順序と、判定方式の移行との関係 | ISS-07・10・20 |
| D11 | 投稿の増加への備え（OpenAI のレート制限・Notion の上限） | ISS-16・33・34 |

## 検証用ワークスペースで確かめること（要再現・要確認）

| 課題 | 確かめること |
|---|---|
| ISS-05 | スレッドの返信に違反を書いて警告し、5 分後に `対応終了` にならないか |
| ISS-06 | Slack のアプリの区分。CloudWatch Logs に `ratelimited` が出ているか |
| ISS-08 | ファイルを添えた投稿・チャンネルにも送った返信のイベントの `subtype` |
| ISS-09 | 受講生が `real_name` を変えられるか（ワークスペースの設定） |
| ISS-18 | ワークスペース一覧 DB に、本番のワークスペースの `team_id` が登録されているか |
| ISS-23 | value が 2,000 文字を超えるボタンを送ったときの Slack の応答（優先度は低い。2026-11-12 の訂正で、今の経路では超えないと分かった） |
| ISS-27 | `team_id` が空のアクティブなレコードの件数 |
