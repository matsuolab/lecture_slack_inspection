"""
1回目の promptfoo eval 結果（JSON export）から、violation_score が
グレーゾーン（既定 21-60）に落ちたテストケースだけを抽出し、
再現性検証（--repeat）用の testcases YAML を作るスクリプト。

## 背景
再現性検証は同じ入力を複数回叩くため、全件（実データ111件など）に対して
行うとコストが N 倍になる。実害が大きいのは「violation_score が閾値
（51）付近でブレて pass/fail が反転するケース」なので、まず1回 eval を
回し、その中でグレーゾーンに落ちたケースだけを対象に --repeat で
ブレを検証する。

## 前提
- 事前に `npx promptfoo eval --output results.json` で1回目の結果を
  JSON エクスポートしておくこと。
- グレーゾーンの範囲は expected_is_violation を問わず、実際に返ってきた
  violation_score のみで判定する（正解ラベルに関わらず、閾値付近の
  スコアが出たケース＝ブレの実害が起きうるケースだから）。
- 複数 provider の結果が含まれる場合、いずれかの provider でグレーゾーンに
  落ちたテストケースを対象にする（テストケース単位で抽出する）。

## 使い方
    python select_gray_zone.py \
        --results results.json \
        --output datasets/gray_zone_testcases.yaml \
        --low 21 --high 60
"""

import argparse
import json
import sys
from pathlib import Path

import yaml


def select_gray_zone(
    results_path: Path,
    low: int,
    high: int,
) -> list[dict]:
    with open(results_path, encoding="utf-8") as f:
        data = json.load(f)

    rows = data["results"]["results"]

    # vars の内容（text など）が同一のテストケースをキーにまとめる。
    # 同じ testCase が複数 provider 分だけ rows に重複して入っているため。
    seen: dict[str, dict] = {}
    for r in rows:
        output = r.get("response", {}).get("output")
        if not isinstance(output, dict):
            continue
        score = output.get("violation_score")
        if score is None:
            continue

        test_case = r.get("testCase", {})
        vars_ = test_case.get("vars", {})
        key = vars_.get("text", "")
        if not key:
            continue

        if low <= score <= high:
            if key not in seen:
                # assert / options はここでは持たせない。promptfooconfig.yml の
                # defaultTest.assert が実行時にマージされるため、ここで testCase
                # をそのまま書き出すと assert が二重になる（1回目 eval 実行時に
                # 既に defaultTest.assert がマージされた状態で記録されているため）。
                seen[key] = {
                    "description": test_case.get("description", ""),
                    "vars": vars_,
                }

    return list(seen.values())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="1回目の eval 結果からグレーゾーンのテストケースだけを抽出する"
    )
    parser.add_argument("--results", required=True, help="1回目の eval 結果 JSON（promptfoo eval --output）")
    parser.add_argument(
        "--output",
        default="datasets/gray_zone_testcases.yaml",
        help="出力先 YAML パス（デフォルト: datasets/gray_zone_testcases.yaml）",
    )
    parser.add_argument("--low", type=int, default=21, help="グレーゾーン下限（デフォルト: 21）")
    parser.add_argument("--high", type=int, default=60, help="グレーゾーン上限（デフォルト: 60）")
    args = parser.parse_args()

    results_path = Path(args.results)
    if not results_path.exists():
        print(f"ERROR: ファイルが見つかりません: {results_path}")
        sys.exit(1)

    test_cases = select_gray_zone(results_path, args.low, args.high)

    if not test_cases:
        print(f"グレーゾーン（{args.low}-{args.high}点）に該当するテストケースはありませんでした。")
        sys.exit(0)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        yaml.dump(test_cases, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    print(f"✓ グレーゾーン（{args.low}-{args.high}点）のテストケース {len(test_cases)} 件を {output_path} に出力しました。")
    print("  この YAML を --repeat 付きで評価すると、同一ケースの繰り返し結果が得られます。")


if __name__ == "__main__":
    main()
