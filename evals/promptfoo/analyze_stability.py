"""
グレーゾーンのテストケースを --repeat で複数回実行した promptfoo eval 結果を集計し、
モデルの出力（violation_score / Detection の pass-fail）がどれだけブレるかを
レポートするスクリプト。

## 背景
LLM は同じ入力に対して常に同じ出力を返すとは限らない（gpt-4o-mini は
temperature: 0 でほぼ決定的だが、gpt-5.6-luna のような reasoning モデルは
内部の推論過程自体に揺らぎがあり、ブレやすい）。
violation_score が閾値（51）付近でブレると、モデルを1回だけ評価した結果で
「合格/不合格」を判断するのは危険（reasoning モデルほど注意が必要）。

## 使い方
    # 1. 1回目の eval を実行し、結果を JSON エクスポート
    npx promptfoo eval --output results.json

    # 2. グレーゾーン（21-60点）のテストケースだけを抽出
    python select_gray_zone.py --results results.json --output datasets/gray_zone_testcases.yaml

    # 3. promptfooconfig.yml の tests: を datasets/gray_zone_testcases.yaml に切り替えてから、
    #    --repeat N で繰り返し実行
    npx promptfoo eval --repeat 5 --output repeat_results.json

    # 4. このスクリプトでブレを集計
    python analyze_stability.py --results repeat_results.json
"""

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

VIOLATION_THRESHOLD = 51


def analyze(results_path: Path) -> dict:
    with open(results_path, encoding="utf-8") as f:
        data = json.load(f)

    rows = data["results"]["results"]

    # (provider_label, text) -> [violation_score, ...]
    groups: dict[tuple[str, str], list[int]] = defaultdict(list)
    descriptions: dict[tuple[str, str], str] = {}

    for r in rows:
        output = r.get("response", {}).get("output")
        if not isinstance(output, dict):
            continue
        score = output.get("violation_score")
        if score is None:
            continue

        provider = r["provider"]["label"]
        text = r["testCase"]["vars"].get("text", "")
        key = (provider, text)
        groups[key].append(score)
        descriptions[key] = r["testCase"].get("description", "")

    report_rows = []
    for (provider, text), scores in groups.items():
        n = len(scores)
        decisions = [1 if s >= VIOLATION_THRESHOLD else 0 for s in scores]
        majority = 1 if sum(decisions) * 2 >= n else 0
        disagree_count = sum(1 for d in decisions if d != majority)
        crosses_boundary = len(set(decisions)) > 1  # pass/fail が反転したケース

        report_rows.append(
            {
                "provider": provider,
                "description": descriptions[(provider, text)],
                "n_runs": n,
                "score_mean": round(statistics.mean(scores), 1),
                "score_stdev": round(statistics.stdev(scores), 1) if n > 1 else 0.0,
                "score_min": min(scores),
                "score_max": max(scores),
                "decision_disagree_count": disagree_count,
                "crosses_boundary": crosses_boundary,
            }
        )

    return {"rows": report_rows}


def print_report(report: dict) -> None:
    rows = report["rows"]
    if not rows:
        print("集計対象のデータがありませんでした。")
        return

    rows_sorted = sorted(rows, key=lambda r: (-r["crosses_boundary"], -r["score_stdev"]))

    print(f"{'provider':<14} {'n':>3} {'mean':>6} {'stdev':>6} {'min':>4} {'max':>4} {'disagree':>8} {'boundary?':>10}")
    for r in rows_sorted:
        print(
            f"{r['provider']:<14} {r['n_runs']:>3} {r['score_mean']:>6} {r['score_stdev']:>6} "
            f"{r['score_min']:>4} {r['score_max']:>4} {r['decision_disagree_count']:>8} "
            f"{'YES' if r['crosses_boundary'] else '':>10}  {r['description'][:50]}"
        )

    print()
    by_provider: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_provider[r["provider"]].append(r)

    print("=== サマリー（provider 別） ===")
    for provider, provider_rows in by_provider.items():
        n_cases = len(provider_rows)
        n_boundary = sum(1 for r in provider_rows if r["crosses_boundary"])
        avg_stdev = round(statistics.mean(r["score_stdev"] for r in provider_rows), 2)
        print(
            f"{provider}: 対象 {n_cases} 件中 {n_boundary} 件 ({n_boundary / n_cases:.1%}) で "
            f"pass/fail が反転。violation_score の平均標準偏差 = {avg_stdev}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="--repeat で複数回実行した eval 結果から出力のブレを集計する"
    )
    parser.add_argument("--results", required=True, help="--repeat 付きで実行した eval 結果 JSON")
    parser.add_argument("--output", help="集計結果を JSON で保存するパス（省略時は標準出力のみ）")
    args = parser.parse_args()

    results_path = Path(args.results)
    if not results_path.exists():
        print(f"ERROR: ファイルが見つかりません: {results_path}")
        sys.exit(1)

    report = analyze(results_path)
    print_report(report)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\n✓ 集計結果を {output_path} に保存しました。")


if __name__ == "__main__":
    main()
