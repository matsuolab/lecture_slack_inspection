# evals — LLM 評価ガイド

このディレクトリでは [promptfoo](https://promptfoo.dev) を使って、Slack 投稿のガイドライン違反判定プロンプト（`judge_violation`）を評価します。

---

## ディレクトリ構成

```
evals/
├── package.json              # promptfoo 依存定義
├── requirements.txt          # Python 依存定義（PyYAML）
└── promptfoo/
    ├── promptfooconfig.yml   # 評価設定（プロバイダー・プロンプト・テストケース参照）
    ├── testcases.yml         # サンプルテストケース（6件）
    ├── load_dataset.py       # CSV → YAML 変換スクリプト
    └── datasets/
        ├── violating_article_legend.csv   # 条文ID → 条文テキスト の対応表
        ├── testcases_preview.yaml         # 先頭5件のプレビュー（Git 管理対象）
        └── testcases.yaml                 # 全件データセット（Git 管理対象外）
```

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
export OPENAI_API_KEY=sk-...
```

---

## 評価の実行

```bash
cd evals/promptfoo
npx promptfoo eval
```


### 結果の確認

```bash
npx promptfoo view
```

ブラウザで対話的な結果ビューアーが開きます。

---

## テストケースの構造

### vars（入力変数）

| フィールド | 説明 | 例 |
|---|---|---|
| `text` | 判定対象の Slack 投稿テキスト | `連絡先は090-1234-5678です。` |
| `articles_text` | プロンプトに渡す関連条文テキスト | `- 12.8: 第12条（便宜上の細分化）: ...` |
| `expected_is_violation` | 正解の違反判定 | `true` / `false` |
| `expected_violating_article` | 正解の違反条文 ID | `12.8` |
| `length` | 投稿の長さカテゴリ | `短文(15-50)` / `中文(50-200)` / `長文(200+)` |
| `intent` | 投稿者の意図 | `悪意なし(無知・うっかり)` / `悪意あり(確信犯)` |
| `degree` | 違反の深刻度 | `明確な違反` / `境界事例` |
| `noise` | ノイズの種類 | `なし` / `丁寧` / `感情的` / `誤字脱字あり` |

### アサーション（スコアリング）

各テストケースは 2 つの JavaScript アサーションで採点されます。

#### メトリクス: `Detection`（pass/fail を決定）

| 条件 | スコア加算 | pass 判定 |
|---|---|---|
| `output.is_violation` が正解と一致 | +1.0 | ◯（これが pass の必須条件） |


- **pass**: `is_violation` の正誤のみで判定
- **score**: 0.0〜1.0（違反ケースと非違反ケースで同じスコア設計）


#### メトリクス: `Article`（条文IDの推論の精度、pass/failには影響しない）

`weight: 0` を指定することで pass/fail・総合スコアには加算されず、UI 上に独立した参考指標として表示されます。

| 条件 | スコア加算 | pass 判定 |
|---|---|---|
| `output.article_id` が正解条文と一致 | +1.0 | — |


- **score**: 0.0〜1.0
- 非違反ケースは自動的にscoreが1.0になるようにしている


#### メトリクス: `confidence`（参考指標、pass/fail には影響しない）

`weight: 0` を指定することで pass/fail・総合スコアには加算されず、UI 上に独立した参考指標として表示されます。

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
| `violating_article` | 違反している条文 ID | `12.8` |
| `length` | 投稿の長さカテゴリ | `短文(15-50)` |
| `intent` | 投稿者の意図 | `悪意なし(無知・うっかり)` |
| `degree` | 違反の深刻度 | `境界事例` |
| `noise` | ノイズの種類 | `丁寧` |

### 変換コマンド

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
