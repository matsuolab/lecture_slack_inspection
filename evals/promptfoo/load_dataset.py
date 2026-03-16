"""
ローカルの CSV ファイルを promptfoo が読み込める YAML 形式に変換するスクリプト。

## 前提
- pip install pyyaml
- datasets/ ディレクトリは .gitignore に追加済み（Git 管理対象外）

## CSV の準備
Google Sheets などで作成した CSV を手動で datasets/ に配置する。
（Google Sheets: ファイル → ダウンロード → CSV）

CSV の列定義:

| 列名               | 説明                                      | 例                           |
|--------------------|-------------------------------------------|------------------------------|
| text               | 検査対象テキスト                          | 連絡先は090-1234-5678です。  |
| is_violation       | 正解の違反判定 (true / false)             | true                         |
| violating_article  | 違反している条文ID                        | 11-ix                        |
| length             | 投稿の長さカテゴリ                        | 短文(15-50)                  |
| intent             | 投稿者の意図                              | 悪意なし(無知・うっかり)     |
| degree             | 違反の深刻度                              | 境界事例                     |
| noise              | ノイズの種類                              | 丁寧                         |

アサーションの挙動:
  【Detection】(違反判定)
  - pass  : output.is_violation が正解と一致するか否か
  - score : is_violation 一致で 1.0、不一致で 0.0

  【Article】(判定理由)
  - pass  : テスト全体の合否に影響を与えないよう、条件に関わらず常に true
  - score : 違反ケースは article_id が正解と完全一致で 1.0、不一致で 0.0
            非違反ケースは判定理由を無視し、無条件で 1.0

## 使い方

    # CSV を datasets/ に配置してから実行
    python load_dataset.py datasets/my_data.csv

    # 出力先を変更したい場合
    python load_dataset.py datasets/my_data.csv --output datasets/testcases.yaml
"""

import argparse
import csv
import sys
from pathlib import Path

import yaml


OUTPUT_DIR = Path(__file__).parent / "datasets"
DEFAULT_OUTPUT = OUTPUT_DIR / "testcases.yaml"
LEGEND_CSV = Path(__file__).parent / "datasets/violating_article_legend.csv"


def load_articles_text(legend_csv_path: Path) -> str:
    """violating_article_legend.csv を読み込み、プロンプト用のテキストに変換する。"""
    with open(legend_csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        lines = [f"- {row['violating_article']}: {row['meaning']}" for row in reader]
    return "\n".join(lines)


def csv_to_promptfoo_yaml(csv_path: str, output_path: Path, limit: int | None = None) -> None:
    """CSV を promptfoo のテストケース YAML に変換する。"""
    required_columns = {
        "text",
        "is_violation",
        "violating_article",
        "length",
        "intent",
        "degree",
        "noise",
    }

    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    if limit is not None:
        rows = rows[:limit]

    if not rows:
        print("ERROR: CSV にデータ行がありません。")
        sys.exit(1)

    missing = required_columns - set(rows[0].keys())
    if missing:
        print(f"ERROR: CSV に必須列が不足しています: {missing}")
        sys.exit(1)

    legend_csv_path = LEGEND_CSV.resolve()
    if not legend_csv_path.exists():
        print(f"ERROR: violating_article_legend.csv が見つかりません: {legend_csv_path}")
        sys.exit(1)
    articles_text = load_articles_text(legend_csv_path)

    testcases = []
    for i, row in enumerate(rows, start=2):  # start=2: ヘッダー行を除いた行番号
        text = row["text"].strip()
        is_violation_str = row["is_violation"].strip().lower()
        violating_article = row["violating_article"].strip()
        length = row["length"].strip()
        intent = row["intent"].strip()
        degree = row["degree"].strip()
        noise = row["noise"].strip()

        if is_violation_str not in ("true", "false"):
            print(f"WARNING: 行 {i} の is_violation が不正 ({is_violation_str!r})。スキップします。")
            continue

        is_violation = is_violation_str == "true"

        # 自動生成の description
        violation_label = "違反" if is_violation else "非違反"
        article_label = f"条文:{violating_article}" if violating_article else "条文:なし"
        description = f"[{violation_label}] {article_label} / {length} / {intent} / {degree}"

        # Detection
        detection_assert_value = (
            "const isCorrect = output.is_violation === context.vars.expected_is_violation;\n"
            "return {\n"
            "  pass: isCorrect,\n"
            "  score: isCorrect ? 1.0 : 0.0,\n"
            "  reason: `Detection - Expected: ${context.vars.expected_is_violation}, Got: ${output.is_violation}`\n"
            "};"
        )

        # Aticle
        article_assert_value = (
            "const expectedIsViolation = context.vars.expected_is_violation;\n"
            "const expectedArticle = context.vars.expected_violating_article || '';\n"
            "const gotArticle = output.article_id || '';\n"
            "let isCorrect = false;\n"
            "let reason = '';\n"
            "if (expectedIsViolation) {\n"
            "  isCorrect = String(expectedArticle) === String(gotArticle);\n"
            "  reason = isCorrect ? 'Match' : `Expected: ${expectedArticle}, Got: ${gotArticle}`;\n"
            "} else {\n"
            "  isCorrect = true;\n"
            "  reason = 'Ignored (Non-violation case)';\n"
            "}\n"
            "return {\n"
            "  pass: true,\n"
            "  score: isCorrect ? 1.0 : 0.0,\n"
            "  reason: `Article Check - ${reason}`\n"
            "};"
        )
      
        confidence_assert_value = (
            "const isViolationCorrect = output.is_violation === context.vars.expected_is_violation;\n"
            "const confidence = output.confidence ?? 0;\n"
            "const degree = context.vars.degree;\n"
            "let score, reason;\n"
            "if (isViolationCorrect) {\n"
            "  score = degree === '明確な違反'\n"
            "    ? (confidence >= 0.8 ? 1.0 : confidence >= 0.6 ? 0.7 : 0.3)\n"
            "    : (confidence >= 0.5 ? 1.0 : 0.5);\n"
            "  reason = `correct, confidence=${confidence} (${degree})`;\n"
            "} else {\n"
            "  score = Math.max(0, 1.0 - confidence);\n"
            "  reason = `wrong prediction, confidence=${confidence} (overconfidence penalty)`;\n"
            "}\n"
            "return { pass: true, score, reason };"
        )

        testcases.append(
            {
                "description": description,
                "vars": {
                    "text": text,
                    "articles_text": articles_text,
                    "expected_is_violation": is_violation,
                    "expected_violating_article": violating_article,
                    "length": length,
                    "intent": intent,
                    "degree": degree,
                    "noise": noise,
                },
                "assert": [
                    {
                        "type": "javascript",
                        "metric": "Detection",
                        "value": detection_assert_value,
                    },
                    {
                        "type": "javascript",
                        "metric": "Article",
                        "value": article_assert_value,
                    },
                    {
                        "type": "javascript",
                        "metric": "confidence",
                        "weight": 0,
                        "value": confidence_assert_value,
                    },
                ],
            }
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        yaml.dump(
            testcases,
            f,
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )

    print(f"✓ {len(testcases)} 件のテストケースを {output_path} に出力しました。")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ローカルの CSV を promptfoo 用 YAML に変換する"
    )
    parser.add_argument(
        "csv_path",
        metavar="CSV_PATH",
        help="変換元の CSV ファイルパス（例: datasets/my_data.csv）",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help=f"出力先 YAML パス (デフォルト: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="先頭 N 件のみ変換する（動作確認用）",
    )
    args = parser.parse_args()

    csv_path = Path(args.csv_path)
    if not csv_path.exists():
        print(f"ERROR: ファイルが見つかりません: {csv_path}")
        print("Google Sheets からエクスポートした CSV を指定のパスに配置してください。")
        sys.exit(1)

    csv_to_promptfoo_yaml(str(csv_path), Path(args.output), limit=args.limit)


if __name__ == "__main__":
    main()
