#!/usr/bin/env python3
"""promptfoo の結果（eval -o の JSON）を、発注書 PJT04 の指標で言語別に集計する。

使い方（evals/promptfoo/ で）:
    python summarize.py output/results.json [output/results_2.json ...] [--csv output/summary.csv]

- 複数の結果のファイル、または --repeat N で流した結果を渡すと、同じ投稿の判定がそろった割合（安定性）も出す。
  率は 1 回目の判定で出し、2 回目以降は安定性と回ごとの再現率・適合率にだけ使う
- 出力は集計値と投稿の id だけ（投稿の本文は出さない）。指標の定義は evals/README.md の「何を計測するか」
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

MAIN_LANGS = ["ja", "en", "zh", "ar", "fr", "sw"]
TAGS = ["category", "degree", "intent", "noise", "length"]


def lang_group(lang: str) -> str:
    return lang if lang in MAIN_LANGS else "mixed"


def load_runs(paths: list[Path]) -> list[dict[str, dict]]:
    """結果を「回」ごとの {id: 1 件} に分ける（同じファイルの中の繰り返しも別の回にする）"""
    by_run: dict[tuple[int, int], dict[str, dict]] = defaultdict(dict)
    for fi, p in enumerate(paths):
        items = json.loads(p.read_text(encoding="utf-8"))["results"]["results"]
        seen: dict[str, int] = defaultdict(int)
        for it in items:
            v = it["vars"]
            k = seen[v["id"]]  # 同じファイルの中の k 回目の出現を、そのファイルの k 回目の回とする
            seen[v["id"]] += 1
            resp = it.get("response") or {}
            out = resp.get("output")
            if not isinstance(out, dict):
                out = {"error": resp.get("error") or it.get("error") or "no output"}
            by_run[(fi, k)][v["id"]] = {"vars": v, "out": out}
    return [by_run[k] for k in sorted(by_run)]


def ratio(a: int, b: int) -> str:
    return f"{a / b:.3f}" if b else "—"


def confusion(run: dict[str, dict]) -> dict[str, dict]:
    stats: dict[str, dict] = defaultdict(lambda: defaultdict(int))
    for rec in run.values():
        v, o = rec["vars"], rec["out"]
        for key in (lang_group(v["language"]), "全体"):
            s = stats[key]
            s["n"] += 1
            if o.get("error"):
                s["err"] += 1
                s["err_pos"] += int(bool(v["expected_is_violation"]))
                continue
            exp, got = bool(v["expected_is_violation"]), bool(o.get("is_violation"))
            s["tp" if exp and got else "fn" if exp else "fp" if got else "tn"] += 1
            if exp and got:
                s["art_n"] += 1
                s["art_ok"] += int(o.get("article_id") == v.get("expected_article"))
    return stats


def pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    if not xs:
        return float("nan")
    k = (len(xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("results", nargs="+", type=Path)
    ap.add_argument("--csv", type=Path, help="言語別の表を CSV にも書く")
    a = ap.parse_args()
    runs = load_runs(a.results)
    first = runs[0]
    st = confusion(first)

    print(f"- 結果: {', '.join(str(p) for p in a.results)}（{len(runs)} 回分。1 回目 {len(first)} 件）")
    print()
    header = ["言語", "件数", "取りこぼし", "誤検知", "再現率", "適合率", "F1", "通知", "判定の失敗", "再現率（失敗を取りこぼしに数えた場合）", "条文の一致"]
    print("| " + " | ".join(header) + " |")
    print("|" + "---|" * len(header))
    rows = []
    for key in MAIN_LANGS + ["mixed", "全体"]:
        s = st.get(key)
        if not s:
            continue
        tp, fn, fp = s["tp"], s["fn"], s["fp"]
        rec = tp / (tp + fn) if tp + fn else 0.0
        prec = tp / (tp + fp) if tp + fp else 0.0
        f1 = f"{2 * rec * prec / (rec + prec):.3f}" if rec + prec else "—"
        row = [key, s["n"], fn, fp, ratio(tp, tp + fn), ratio(tp, tp + fp), f1, tp + fp, s["err"],
               ratio(tp, tp + fn + s["err_pos"]), f"{s['art_ok']}/{s['art_n']}"]
        rows.append(row)
        print("| " + " | ".join(str(x) for x in row) + " |")
    if a.csv:
        a.csv.parent.mkdir(parents=True, exist_ok=True)
        with open(a.csv, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)

    # 間違えた投稿（id だけ）。取りこぼしの分析のため、警告の確率と条文の候補の上位も出す
    print()
    print("**間違えた投稿（1 回目）**")
    print()
    print("| id | 正解 | 判定 | 方法 | P(warn) | 正解の条文 | 判定の条文 | 条文の候補（上位 3） |")
    print("|---|---|---|---|---|---|---|---|")
    for pid, rec in sorted(first.items()):
        v, o = rec["vars"], rec["out"]
        wrong = o.get("error") or bool(o.get("is_violation")) != bool(v["expected_is_violation"])
        art_wrong = (not o.get("error") and v["expected_is_violation"] and o.get("is_violation")
                     and o.get("article_id") != v.get("expected_article"))
        if wrong or art_wrong:
            top = ", ".join(f"{x[0]}({x[1]:.2f})" for x in (o.get("article_top3") or []))
            pw = f"{o['p_warn']:.3f}" if isinstance(o.get("p_warn"), (int, float)) else "—"
            got = "失敗" if o.get("error") else ("違反" if o.get("is_violation") else "違反なし")
            print(f"| {pid} | {'違反' if v['expected_is_violation'] else '違反なし'} | {got} | {o.get('method') or '—'} | {pw} | {v.get('expected_article') or '—'} | {o.get('article_id') or '—'} | {top or '—'} |")

    # 言語の間の一致
    by_sc: dict[str, dict[str, bool]] = defaultdict(dict)
    for rec in first.values():
        v, o = rec["vars"], rec["out"]
        if v["language"] in MAIN_LANGS and not o.get("error"):
            by_sc[v["scenario_id"]][v["language"]] = bool(o.get("is_violation"))
    full = [sc for sc, d in by_sc.items() if len(d) == len(MAIN_LANGS)]
    split = sorted(sc for sc in full if len(set(by_sc[sc].values())) > 1)
    print()
    print(f"- 言語の間の一致: 6 言語がそろったシナリオ {len(full)} 件のうち、判定が全言語で同じ {len(full) - len(split)} 件（{ratio(len(full) - len(split), len(full))}）"
          + (f"。分かれたシナリオ: {', '.join(split)}" if split else ""))

    # 安定性（2 回以上あるとき）
    if len(runs) > 1:
        ids = set.intersection(*(set(r) for r in runs))
        same = sum(1 for i in ids if len({(r[i]['out'].get('error') is not None, r[i]['out'].get('is_violation')) for r in runs}) == 1)
        print(f"- 判定の安定性: {len(runs)} 回とも判定が同じ {same}/{len(ids)} 件（{ratio(same, len(ids))}）")
        for k, r in enumerate(runs, 1):
            s = confusion(r)["全体"]
            print(f"  - {k} 回目: 取りこぼし {s['fn']}・誤検知 {s['fp']}・判定の失敗 {s['err']}・再現率 {ratio(s['tp'], s['tp'] + s['fn'])}・適合率 {ratio(s['tp'], s['tp'] + s['fp'])}")

    # 予備経路・API の応答コード
    methods: dict[str, int] = defaultdict(int)
    statuses: dict[int, int] = defaultdict(int)
    for rec in first.values():
        methods[rec["out"].get("method") or "失敗"] += 1
        for c in rec["out"].get("calls") or []:
            if c.get("api") == "decisions":
                statuses[c.get("status")] += 1
    print(f"- 判定の方法: {dict(methods)}。Decisions API の応答コード（再試行を含む）: {dict(statuses)}")

    # 時間と費用
    ok = [rec["out"] for rec in first.values() if not rec["out"].get("error")]
    lat = [o["latency_sec"] for o in ok if o.get("latency_sec") is not None]
    if lat:
        print(f"- 判定時間（秒）: 中央値 {statistics.median(lat):.2f}、95 パーセンタイル {pct(lat, 0.95):.2f}、最大 {max(lat):.2f}")
    warn_cost = [o["cost_usd"] for o in ok if o.get("is_violation")]
    safe_cost = [o["cost_usd"] for o in ok if not o.get("is_violation")]
    if ok:
        avg = sum(o["cost_usd"] for o in ok) / len(ok)
        line = f"- 費用: このデータセットでの 1 投稿あたり平均 {avg:.6f} ドル"
        if warn_cost and safe_cost:
            cw, cs = statistics.mean(warn_cost), statistics.mean(safe_cost)
            conv = "、".join(f"違反の割合 {int(p * 100)}% なら 1 投稿 {cs * (1 - p) + cw * p:.6f} ドル・月 3 万投稿で約 {(cs * (1 - p) + cw * p) * 30000:.1f} ドル" for p in (0.01, 0.05))
            line += f"（警告した投稿 {cw:.6f}・警告しない投稿 {cs:.6f}）。本番の割合に直すと: {conv}"
        print(line)

    # 区分ごとの内訳
    print()
    print("**区分ごとの取りこぼし・誤検知（1 回目、全言語）**")
    for tag in TAGS:
        agg: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])  # 件数, 取りこぼし, 誤検知, 失敗
        for rec in first.values():
            v, o = rec["vars"], rec["out"]
            a_ = agg[v.get(tag) or "—"]
            a_[0] += 1
            if o.get("error"):
                a_[3] += 1
            elif v["expected_is_violation"] and not o.get("is_violation"):
                a_[1] += 1
            elif not v["expected_is_violation"] and o.get("is_violation"):
                a_[2] += 1
        bad = {k: x for k, x in agg.items() if x[1] or x[2] or x[3]}
        if bad:
            print(f"- {tag}: " + "、".join(f"{k}（{x[0]} 件中 取りこぼし {x[1]}・誤検知 {x[2]}" + (f"・失敗 {x[3]}" if x[3] else "") + "）" for k, x in sorted(bad.items())))
        else:
            print(f"- {tag}: 間違いなし")
    return 0


if __name__ == "__main__":
    sys.exit(main())
