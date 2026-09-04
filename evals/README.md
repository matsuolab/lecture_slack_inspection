# evals — LLM 評価ガイド

このディレクトリでは [promptfoo](https://promptfoo.dev) を使って、Slack 投稿のガイドライン違反判定プロンプト（`judge_violation`）を評価します。

---

## ディレクトリ構成

```
evals/
├── package.json                  # promptfoo 依存定義
├── requirements.txt              # Python 依存定義（PyYAML）
└── promptfoo/
    ├── promptfooconfig.yml       # 評価設定（プロバイダー・プロンプト・テストケース参照）
    ├── load_dataset.py           # CSV → YAML 変換スクリプト
    ├── convert_notion_export.py  # Notion 違反DB + 条文マスターDB の生JSON → CSV 変換スクリプト
    └── datasets/
        ├── sample_testcases.csv    # 動作確認用の仮データ（Git 管理対象）
        ├── testcases_preview.yaml  # sample_testcases.csv から生成したプレビュー（Git 管理対象）
        ├── real_testcases.csv      # Notion 実データから変換した CSV（Git 管理対象外）
        └── testcases.yaml          # 評価対象データセット（Git 管理対象外、都度 load_dataset.py で生成）
```

> `evals/promptfoo/testcases.yml`（ルート直下、1件のみ）は初期セットアップ当時の名残で、現在の `promptfooconfig.yml` からは参照されておらず、条文ID体系・レスポンス形式（`is_violation` フィールド）も現行仕様と食い違っているため参考にしないこと。

条文の凡例（`articles_text` の元データ）は `lambda/common/data/articles.json`（本番の条文マスタ）をそのまま参照します。CSV での別管理はしていません。

実際の評価に使うテストケース（`text` / `is_violation` などの行データ）は守秘義務の関係上 Git 管理せず、以下のいずれかで各自 `datasets/` に用意します。`sample_testcases.csv` はリポジトリ内で動作確認するための仮データです。

- **Notion の実データから変換する**（推奨、詳細は [Notion 実データからの変換](#notion-実データからの変換) 参照）
- Google Sheets 等で作成した CSV を手動で `datasets/` に配置する

---

## セットアップ

### 1. Node.js（promptfoo）

```bash
cd evals
npm install
```

### 2. Python（データセット変換スクリプト）
- パッケージマネージャはuvやcondaなどでも可

```bash
cd evals
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. 環境変数

`OPENAI_API_KEY` が設定されている必要があります。

```bash
cd evals/promptfoo
cp .env.sample .env
# .env を編集して OPENAI_API_KEY=sk-... を実際のキーに書き換える
```

---

## 評価対象モデル（providers）

`promptfooconfig.yml` の `providers` に複数モデルを並べており、同じデータセットに対して両方が実行され、結果が並んで比較できます。

| label | id | 備考 |
|---|---|---|
| `gpt-4o-mini` | `openai:chat:gpt-4o-mini` | `temperature: 0` |
| `gpt-5.6-luna` | `openai:chat:gpt-5.6-luna` | reasoning モデルのため `temperature` 非対応（渡すと 400 Bad Request）。`reasoning_effort: medium`（既定値）を明示指定 |

比較対象モデルを増やす場合は `providers` に追記してください。reasoning モデル（GPT-5系等）を追加する際は `temperature` を使わず `reasoning_effort` を使うこと。

---

## 評価の実行

```bash
cd evals/promptfoo
npx promptfoo eval
```

`promptfooconfig.yml` の `tests:` が参照している YAML（既定では動作確認用の `datasets/testcases_preview.yaml`）に対して、`providers` の全モデルが実行されます。実データで評価したい場合は事前に [Notion 実データからの変換](#notion-実データからの変換) を行い、`tests:` の参照先を切り替えてください。

### 結果の確認

```bash
npx promptfoo view
```

ブラウザで対話的な結果ビューアーが開き、モデルごとの pass/fail・スコア・レイテンシが列で比較できます。

---

## テストケースの構造

### vars（入力変数）

| フィールド | 説明 | 例 |
|---|---|---|
| `text` | 判定対象の Slack 投稿テキスト | `連絡先は090-1234-5678です。` |
| `articles_text` | プロンプトに渡す関連条文テキスト（`lambda/common/data/articles.json` から自動生成） | `- 11-ix AI Community参加規約 第11条(ix): ...` |
| `expected_is_violation` | 正解の違反判定 | `true` / `false` |
| `expected_violating_article` | 正解の違反条文 ID | `11-ix` |
| `length` | 投稿の長さカテゴリ | `短文(15-50)` / `中文(50-200)` / `長文(200+)` |
| `intent` | 投稿者の意図 | `悪意なし(無知・うっかり)` / `悪意あり(確信犯)` |
| `degree` | 違反の深刻度 | `明確な違反` / `境界事例` |
| `noise` | ノイズの種類 | `なし` / `丁寧` / `感情的` / `誤字脱字あり` |

### アサーション（スコアリング）

各テストケースは 3 つの JavaScript アサーションで採点されます。

#### メトリクス: `Detection`（pass/fail を決定する唯一のメトリクス）

`output.violation_score >= 51` を違反判定（`1`）とみなし、`expected_is_violation` と一致すれば pass。

- **pass / score**: 一致で `pass: true, score: 1.0`、不一致で `pass: false, score: 0.0`
- `Article` / `confidence` は `weight: 0` のため pass/fail・総合スコアには反映されず、UI 上の参考指標としてのみ表示される
- スコアの大小（51点と95点の違いなど）は問わない二値判定


#### メトリクス: `Article`（条文IDの推論の精度、参考指標）

- 違反ケース（`expected_is_violation: true`）: `output.article_id` が正解条文と**完全一致**で `score: 1.0`、不一致で `0.0`
- 非違反ケース: 判定理由を無視し、無条件で `score: 1.0`
- 近縁の条文を外した場合と全く見当違いの条文を選んだ場合を区別しない点に注意

#### メトリクス: `confidence`（過信/過小評価の傾向を見る参考指標）

| 状況 | スコアリングロジック |
|---|---|
| 正解 + `degree: 明確な違反` | confidence ≥ 0.8 → 1.0 / ≥ 0.6 → 0.7 / それ以下 → 0.3 |
| 正解 + `degree: 境界事例` | confidence ≥ 0.5 → 1.0 / それ以下 → 0.5 |
| 誤判定（過信ペナルティ） | `max(0, 1.0 - confidence)` |

> **追加メトリクスを増やす場合**: 同様に `metric` ラベルを付けた `type: javascript` アサーションを `assert` 配列に追加してください。`weight: 0` にすると pass/fail に影響せず参考値のみを記録できます。

---

## データセットの準備（CSV → YAML 変換）

### CSV の列定義

Google Sheets でデータセットを作成し、CSV でエクスポートします。

| 列名 | 説明 | 例 |
|---|---|---|
| `text` | 検査対象テキスト | `連絡先は090-1234-5678です。` |
| `is_violation` | 正解の違反判定 | `true` / `false` |
| `violating_article` | 違反している条文 ID（`lambda/common/data/articles.json` の `id` に対応） | `11-ix` |
| `length` | 投稿の長さカテゴリ | `短文(15-50)` |
| `intent` | 投稿者の意図 | `悪意なし(無知・うっかり)` |
| `degree` | 違反の深刻度 | `境界事例` |
| `noise` | ノイズの種類 | `丁寧` |

`articles_text`（条文の凡例）は CSV の列ではなく、`lambda/common/data/articles.json` から自動生成されます。

### 動作確認（仮データで試す）

実データを用意していなくても、リポジトリ同梱の `datasets/sample_testcases.csv` で動作確認できます。

```bash
cd evals/promptfoo
source ../.venv/bin/activate

python load_dataset.py datasets/sample_testcases.csv --output datasets/testcases_preview.yaml
```

### 変換コマンド（実データ）

実データは守秘義務の関係で Git 管理せず、Google Drive 等で配布された CSV を各自 `datasets/` に配置して使います。

```bash
# evals/ 直下で .venv を有効化してから実行
cd evals/promptfoo
source ../.venv/bin/activate

# 全件変換（出力: datasets/testcases.yaml）
python load_dataset.py datasets/my_data.csv

# 動作確認用：先頭 N 件のみ変換
python load_dataset.py datasets/my_data.csv --limit 5 --output datasets/testcases_preview.yaml

# 出力先を明示したい場合
python load_dataset.py datasets/my_data.csv --output datasets/testcases.yaml
```

---

## Notion 実データからの変換

Slack 上の実際のやり取りを教師データとして使う場合、Notion の「違反」DB（`NOTION_DB_ID`）と条文マスターDB（`NOTION_ARTICLES_DB_ID`）のエクスポート（Notion API のクエリ結果を JSON でダンプしたもの）から評価用 CSV を生成できます。

### 前提となるデータの性質

「違反」DB は `判定結果=Violation`（LLM が違反として検出したもの）のみを記録する設計のため、エクスポートには非違反の投稿は含まれません。実質的な正解ラベルは `対応ステータス` が担っています。

| 対応ステータス | 意味 | 変換後の `is_violation` |
|---|---|---|
| 対応終了 / 期限超過 | 実際に対応された＝真の違反 | `true` |
| 対応不要 | LLM の誤検知＝きわどい非違反（境界事例） | `false` |
| 未対応 | まだ判断が確定していない | **既定では除外**（`--include-unhandled` で `true` として含めることも可能） |

`未対応` を既定で除外しているのは、対応が完了していないだけの真の違反と、放置されている誤検知が区別できず、ラベルの信頼性が低いためです。

### 変換コマンド

```bash
cd evals/promptfoo
source ../.venv/bin/activate

python convert_notion_export.py \
  --violations /path/to/violation_data.json \
  --articles /path/to/articles.json \
  --output datasets/real_testcases.csv

# load_dataset.py で promptfoo 用 YAML に変換（uv で pyyaml を都度導入する場合）
uv run --with pyyaml python load_dataset.py datasets/real_testcases.csv --output datasets/testcases.yaml
```

`--articles` に渡すのは条文マスターDB（`条文ID` / `規約` 等のプロパティを持つ Notion エクスポート）で、`lambda/common/data/articles.json`（本番の条文マスタ、`articles_text` の生成に使う別ファイル）とは異なるので注意してください。

`--violations` / `--articles` のパスにファイルが無い場合、`--fetch` を付けると Notion API から直接取得して保存できます（`.env` に `NOTION_API`（Notion Integration Token）の設定が必要）。

```bash
python convert_notion_export.py \
  --violations /path/to/violation_data.json \
  --articles /path/to/articles.json \
  --output datasets/real_testcases.csv \
  --fetch
```

### 注意点

- `violation_data.json` / `articles.json` などの Notion 生エクスポートは受講者の実投稿を含む機微データです。リポジトリのルートに置く場合は `.gitignore` で除外済みですが、それでもファイルの取り扱い（共有範囲・保存場所）には注意してください。
- 変換後の `intent` / `noise` 列は Notion 側に対応データがないため常に空欄になります。この 2 軸で分析したい場合は変換後に手動で値を埋めてください。
- `real_testcases.csv` から `testcases.yaml` を生成しただけでは評価対象になりません。`promptfooconfig.yml` の `tests:` は既定で動作確認用の `datasets/testcases_preview.yaml` を指しているため、実データを評価する直前に手動で参照先を書き換えてください。

  ```diff
   tests:
  -  - file://datasets/testcases_preview.yaml
  +  - file://datasets/testcases.yaml
  ```

  動作確認用データセットに戻す場合は元の行に書き戻してください。両方を毎回のコマンドで切り替えたくない場合は、`tests:` に両方の YAML をリストで並べても構いません（`promptfooconfig.yml` の `tests:` は複数ファイルを指定できます）。
