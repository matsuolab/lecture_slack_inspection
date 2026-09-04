"""
Notion からエクスポートした「違反」DB + 条文マスターDB の生 JSON を、
load_dataset.py が読み込める CSV 形式に変換するスクリプト。

## 前提
- 「違反」DB のエクスポートは 判定結果=Violation でフィルタされたものを想定
  （Notion API のクエリ結果をそのまま json.dump したもの）
- 対応ステータス で正解ラベルを決める:
  - 対応終了 / 期限超過 : 実際に対応された = 真の違反 (is_violation=true)
  - 対応不要            : LLM が違反と誤検知したが実際は違反ではなかった
                          = きわどい境界事例 (is_violation=false)
  - 未対応              : まだ判断が確定していない（放置されているだけの
                          可能性がある）ため、正解ラベルの信頼性が低い。
                          このスクリプトではデフォルトで除外する。
- 条文マスターDB のエクスポートから 条文ページID → 条文ID（例: 11-ix）の
  対応表を作り、「違反」DB の 対象条文（relation）を articles.json の id に変換する。

## 出力
- text / is_violation / violating_article / length / intent / degree / noise
  の7列を持つ CSV を出力する。
- intent / noise は Notion 側に対応データが無いため常に空欄。
  必要な範囲は評価者が手動で埋める想定。

## 注意
- 入力・出力ともに受講者の実投稿を含む機微データ。
  出力先は .gitignore 済みの datasets/ 配下にすること。

## 使い方
    python convert_notion_export.py \
        --violations ../../violation_data.json \
        --articles ../../articles.json \
        --output datasets/real_testcases.csv
"""

import argparse
import csv
import json
import sys
from pathlib import Path

TRUE_VIOLATION_STATUSES = {"対応終了", "期限超過"}
FALSE_VIOLATION_STATUSES = {"対応不要"}
# 未対応 は正解ラベルが未確定のため、デフォルトでは変換対象から除外する。

SEVERITY_TO_DEGREE = {
    "high": "明確な違反",
    "medium": "境界事例",
}


def _plain_text(prop: dict, key: str) -> str:
    return "".join(t.get("plain_text", "") for t in prop.get(key, []))


def _select_name(prop: dict) -> str | None:
    select = prop.get("select")
    return select.get("name") if select else None


def build_article_id_map(articles_json_path: Path) -> dict[str, str]:
    """条文マスターDB のエクスポートから 条文ページID -> 条文ID の対応表を作る。"""
    with open(articles_json_path, encoding="utf-8") as f:
        data = json.load(f)

    id_map: dict[str, str] = {}
    for page in data["results"]:
        page_id = page["id"]
        article_id = _plain_text(page["properties"]["条文ID"], "title").strip()
        if article_id:
            id_map[page_id] = article_id
    return id_map


def classify_length(text: str) -> str:
    n = len(text)
    if n < 50:
        return "短文(15-50)"
    if n < 200:
        return "中文(50-200)"
    return "長文(200+)"


def convert(
    violations_path: Path,
    articles_path: Path,
    output_path: Path,
    include_unhandled: bool = False,
) -> None:
    article_id_map = build_article_id_map(articles_path)

    with open(violations_path, encoding="utf-8") as f:
        data = json.load(f)

    rows = []
    skipped_empty_text = 0
    skipped_unconfirmed = 0
    for page in data["results"]:
        p = page["properties"]

        text = _plain_text(p["投稿内容"], "title").strip()
        if not text:
            skipped_empty_text += 1
            continue

        status = _select_name(p["対応ステータス"])
        if status in TRUE_VIOLATION_STATUSES:
            is_violation = True
        elif status in FALSE_VIOLATION_STATUSES:
            is_violation = False
        elif include_unhandled:
            # 未対応: 正解ラベルとしては信頼度が低いが、呼び出し側の指定で含める。
            is_violation = True
        else:
            skipped_unconfirmed += 1
            continue

        violating_article = ""
        relations = p["対象条文"].get("relation", [])
        if relations:
            violating_article = article_id_map.get(relations[0]["id"], "")

        degree = ""
        if is_violation:
            severity = _select_name(p["重大度"])
            degree = SEVERITY_TO_DEGREE.get(severity, "")

        rows.append(
            {
                "text": text,
                "is_violation": "true" if is_violation else "false",
                "violating_article": violating_article,
                "length": classify_length(text),
                "intent": "",
                "degree": degree,
                "noise": "",
            }
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["text", "is_violation", "violating_article", "length", "intent", "degree", "noise"],
        )
        writer.writeheader()
        writer.writerows(rows)

    n_violation = sum(1 for r in rows if r["is_violation"] == "true")
    n_non_violation = len(rows) - n_violation
    print(f"✓ {len(rows)} 件を {output_path} に出力しました。")
    print(f"  内訳: is_violation=true {n_violation} 件 / is_violation=false（対応不要） {n_non_violation} 件")
    if skipped_empty_text:
        print(f"  WARNING: 投稿内容が空のレコードを {skipped_empty_text} 件スキップしました。")
    if skipped_unconfirmed:
        print(
            f"  未対応（正解ラベル未確定）のレコードを {skipped_unconfirmed} 件除外しました。"
            " 含めたい場合は --include-unhandled を指定してください。"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Notion 違反DB + 条文マスターDB の生 JSON を評価用 CSV に変換する"
    )
    parser.add_argument(
        "--violations",
        required=True,
        help="Notion 違反DB のエクスポート JSON パス",
    )
    parser.add_argument(
        "--articles",
        required=True,
        help="Notion 条文マスターDB のエクスポート JSON パス",
    )
    parser.add_argument(
        "--output",
        default="datasets/real_testcases.csv",
        help="出力先 CSV パス（デフォルト: datasets/real_testcases.csv）",
    )
    parser.add_argument(
        "--include-unhandled",
        action="store_true",
        help="対応ステータス=未対応（正解ラベル未確定）のレコードも is_violation=true として含める",
    )
    args = parser.parse_args()

    violations_path = Path(args.violations)
    articles_path = Path(args.articles)

    if not violations_path.exists():
        print(f"ERROR: ファイルが見つかりません: {violations_path}")
        sys.exit(1)
    if not articles_path.exists():
        print(f"ERROR: ファイルが見つかりません: {articles_path}")
        sys.exit(1)

    convert(violations_path, articles_path, Path(args.output), include_unhandled=args.include_unhandled)


if __name__ == "__main__":
    main()
