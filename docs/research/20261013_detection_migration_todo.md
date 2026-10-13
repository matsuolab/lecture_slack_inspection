# TODO: 違反検知の判定方式の移行（gpt-4o-mini → Decisions API / gpt-6-luna）

- 作成日: 2026-10-13
- 関連: [報告書](20261013_detection_model_migration.md)、ブランチ `feat/decisions-detection`

## 設定の出どころ（変えるときの注意）

Lambda の環境変数は CDK のデプロイのたびに上書きされる。**AWS コンソールで Lambda の環境変数を直接変えない**（次のデプロイで元に戻り、事故になる）。

| 設定 | 出どころ | 変え方 |
|---|---|---|
| `OPENAI_MODEL` | GitHub Actions の変数 `OPENAI_MODEL` → `deploy.yml` → CDK パラメータ `OpenAIModel` → Lambda | GitHub の変数を変えて Manual Deploy |
| `DETECTION_MODE` | CDK パラメータ `DetectionMode`（既定 `auto`） | 普段は変えない。緊急時だけ `deploy.yml` に `--parameters DetectionMode=...` を足して Manual Deploy |
| OpenAI の API キー | SSM `/openai/api/key`（SecureString） | 変えない。ただし新しいモデルと Decisions API が使えるか確認する |

## 移行（この順で）

| # | やること | 担当 | 状態 |
|---|---|---|---|
| 1 | `DetectionMode=auto` の実装を終え、テストと `cdk synth` を通して commit する | Claude | 済（b0e0d40） |
| 2 | **本番の API キーで gpt-6-luna と Decisions API が使えるか確認する**。実験は手元の `.env` のキーで行ったので、SSM のキーが同じ組織・同じ権限とは限らない。Decisions API は Public Beta で「Decision API is not enabled for this user」（403）になった例が報告されている。使えないまま切り替えると、毎回 Decisions API が失敗して予備経路（gpt-6-luna）になる | 運営 | 未 |
| 3 | `git push -u origin feat/decisions-detection` で上げ、PR を作る（main に直接 push しない） | 運営 | 未 |
| 4 | PR のレビューとマージ。CI（pytest・cdk synth）が通ることを確認する | チーム | 未 |
| 5 | GitHub の Settings → Secrets and variables → Actions → Variables で `OPENAI_MODEL` を `gpt-6-luna` に変える（`DetectionMode` は既定の `auto` のままでよい。`deploy.yml` の変更は不要） | 運営 | 未 |
| 6 | Actions の Manual Deploy（`deploy.yml`）を実行する | 運営 | 未 |
| 7 | デプロイ後の確認（下の「確認」） | 運営 | 未 |
| 8 | 数日間、通知の件数・管理者の却下の割合・取りこぼしの報告を見る。報告書の値（通知は今の約 4 割、通知のうち警告すべきものが約 6 割）と大きく違わないか | 運営 | 未 |

### 確認（手順 7）

- Notion の違反 DB の新しいレコードの「検出方法」が `Decisions` になっている（今の方式は `LLM`）
- 「検出方法」が `LLM-fallback`（Decisions API が失敗して予備経路で判定）のレコードが頻発していない。頻発するなら手順 2 のキーの権限を疑う
- （参考）CloudWatch Logs に `detection_mode=decisions (auto, ...)` が出ることがある。標準の logging の info なので、Lambda のログレベルの設定によっては出ない
- 検証用ワークスペースで `docs/regression_checklist.md` の 1 章（違反検知 → アラート）と 3 章（投稿の編集）
- 追加の確認:
  - 外部チャットグループの招待リンクを含む投稿 → 管理者チャンネルに通知され、Notion の条文が `11-xiii`
  - 自己紹介だけの投稿・講座の公式資料（Zoom・Drive）への案内 → 通知されない
  - バックスラッシュを含む投稿（例: 正規表現の `\d` の質問）でもエラーにならない
  - 宿題の答えを求める投稿 → 条文が `course-8-iv`（切り詰められていない）で Notion にリレーションが付く

### ロールバック

- 全体を戻す: GitHub の `OPENAI_MODEL` を `gpt-4o-mini` に戻して Manual Deploy（`auto` で今の方式に戻る。gpt-4o-mini が使える間だけ）
- Decisions API だけが止まった: `deploy.yml` に `--parameters DetectionMode=luna` を足して Manual Deploy（gpt-6-luna だけの経路に固定）
- やってはいけない: `DetectionMode=llm_score` と `OPENAI_MODEL=gpt-6-luna` の組み合わせ（今のプロンプトのまま gpt-6-luna で判定し、取りこぼしが大きく増える）

## 移行のあと・別件

| # | やること | 担当 | 備考 |
|---|---|---|---|
| A | Slack の 3 秒ルール: 判定を非同期にする方式を調べて決める（Slack にはすぐ 200、判定は Lambda の非同期呼び出しか SQS） | 運営が調査 | 8 月のログで判定 32 件中 30 件が Slack の再送になっていた。失敗が続くと Slack がイベント購読を止めるおそれ |
| B | 条文の正解ラベルを付け、条文の選択を採点する（177 行） | 運営 → Claude | `slack_detection_eval/labels/article_labels.csv` → `score_articles.py` |
| C | gpt-4o-mini の廃止後、`llm_score` の経路・`judge_violation.txt`・temperature の送り直しを削除する PR | Claude | 差分はほぼ削除だけ |
| D | 条文を Notion の条文マスタから読み込む | 要件の確認から | Notion は有効・共通 22 件、リポジトリは 31 件で 9 件足りない（community-4・edu-3-ii など） |
| E | コードの中に直接書いた指示文をプロンプトのファイルに出す／JSON 出力の部分の改善 | 要件の確認から | |
| F | まだ誰も見ていない新しい判断（今日以降の Notion）で評価し直す | Claude | 1〜2 週間ほど溜まってから |
| G | `app_batch` の既存の問題: ローカルルールを渡していない・予備経路だと 15 分で時間切れになりうる・方式名を上書きしている | 未定 | |
| H | GAS 用 IAM アクセスキーのシークレットを取り出せない（`infra_stack.py`） | 運営が方針を決める | Secrets Manager に保存するか、キーを手動発行にするか |
| I | `infra/tests` を今のスタックに合わせて書き直す | 未定 | CI では実行していない |
| J | 作業ツリーの `judge_violation.txt` の壊れた未コミットの変更を戻すか決める | 運営 | 1 行目に別の文が割り込んでいる |
