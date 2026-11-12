#!/usr/bin/env python3
"""PJT04 の評価用データセット（eval_posts.csv または .jsonl）を、promptfoo のテストケース（YAML）に変換する。

使い方（evals/promptfoo/ で）:
    python load_dataset.py <eval_posts.csv|eval_posts.jsonl> [--output datasets/testcases.yaml] [--limit N]

- 条文の ID（violating_article）が lambda/common/data/articles.json にあるかを確かめ、無ければ止める
- 列: id・scenario_id・language・text・is_violation・violating_article・category・length・intent・degree・noise・note
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
ARTICLES = REPO / "lambda" / "common" / "data" / "articles.json"
REQUIRED = ["id", "scenario_id", "language", "text", "is_violation", "violating_article"]
TAGS = ["category", "length", "intent", "degree", "noise"]


def _bool(v) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("true", "1", "yes"):
        return True
    if s in ("false", "0", "no"):
        return False
    raise ValueError(f"is_violation が真偽値でない: {v!r}")


def read_rows(path: Path) -> list[dict]:
    if path.suffix == ".jsonl":
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def to_testcases(rows: list[dict], article_ids: set[str]) -> list[dict]:
    seen = set()
    cases = []
    for i, r in enumerate(rows, 1):
        missing = [c for c in REQUIRED if c not in r]
        if missing:
            raise ValueError(f"{i} 行目: 列が無い {missing}")
        if r["id"] in seen:
            raise ValueError(f"{i} 行目: id が重複している {r['id']}")
        seen.add(r["id"])
        is_v = _bool(r["is_violation"])
        art = (r.get("violating_article") or "").strip()
        if is_v and not art:
            raise ValueError(f"{r['id']}: 警告すべき投稿なのに条文が無い")
        if not is_v and art:
            raise ValueError(f"{r['id']}: 警告しない投稿なのに条文がある")
        if art and art not in article_ids:
            raise ValueError(f"{r['id']}: 条文 {art} が articles.json に無い")
        vars_ = {
            "id": r["id"], "scenario_id": r["scenario_id"], "language": r["language"], "text": r["text"],
            "expected_is_violation": is_v, "expected_article": art,
        }
        for t in TAGS:
            vars_[t] = r.get(t, "")
        cases.append({"description": f"{r['id']} [{'違反' if is_v else '非違反'}] {r.get('category', '')}", "vars": vars_})
    return cases


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dataset", type=Path)
    ap.add_argument("--output", type=Path, default=HERE / "datasets" / "testcases.yaml")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    article_ids = {x["id"] for x in json.loads(ARTICLES.read_text(encoding="utf-8"))["articles"]}
    rows = read_rows(a.dataset)
    if a.limit:
        rows = rows[: a.limit]
    cases = to_testcases(rows, article_ids)
    a.output.parent.mkdir(parents=True, exist_ok=True)
    a.output.write_text(yaml.safe_dump(cases, allow_unicode=True, sort_keys=False), encoding="utf-8")
    n_v = sum(1 for c in cases if c["vars"]["expected_is_violation"])
    print(f"{len(cases)} 件（警告すべき {n_v}・警告しない {len(cases) - n_v}）を {a.output} に書いた")
    return 0


if __name__ == "__main__":
    sys.exit(main())
