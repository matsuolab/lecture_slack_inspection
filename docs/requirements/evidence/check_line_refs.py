#!/usr/bin/env python3
"""文書に書いた `パス:行` の参照を、基準のコミットのファイルと機械的に照合する。

使い方（lecture_slack_inspection/ で）:
    python3 docs/requirements/evidence/check_line_refs.py <基準のコミット> <文書>... [--show]

- 文書の中のバッククォートで囲んだ `xxx.py:12`、`xxx.py:12-20`、`xxx.py:12-20, 30-35` を拾う
- ファイル名は、基準のコミットのファイルの一覧から後方一致で探す（`handler.py` のように複数に当たるときは「曖昧」として、どれか 1 つで範囲に収まれば可とする）
- 行番号が、基準のコミットのファイルの行数を超えていたら「範囲外」
- --show を付けると、参照ごとに基準のコミットのその行を表示する（中身が記述と合っているかは人が見る）
- 作業ツリーではなく、必ず基準のコミット（git show <基準>:<パス>）で数える。作業ツリーで数えると、未コミットの変更の分だけずれる

終了コード: 0 = 範囲外なし、1 = 範囲外あり、2 = 使い方の誤り
"""
from __future__ import annotations

import re
import subprocess
import sys

REF_RE = re.compile(r"`([A-Za-z0-9_./-]+\.(?:py|txt|json|yml|yaml|ts|md|sh)):([0-9][0-9,\- ]*)`")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def parse_ranges(spec: str) -> list[tuple[int, int]]:
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.append((int(a), int(b)))
        else:
            out.append((int(part), int(part)))
    return out


def main(argv: list[str]) -> int:
    show = "--show" in argv
    args = [a for a in argv if a != "--show"]
    if len(args) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    base, docs = args[0], args[1:]
    files = git("ls-tree", "-r", "--name-only", base).splitlines()
    cache: dict[str, list[str]] = {}

    def lines_of(path: str) -> list[str]:
        if path not in cache:
            cache[path] = git("show", f"{base}:{path}").splitlines()
        return cache[path]

    total = bad = ambiguous = missing = 0
    for doc in docs:
        with open(doc, encoding="utf-8") as f:
            for lineno, text in enumerate(f, 1):
                for m in REF_RE.finditer(text):
                    name, spec = m.group(1), m.group(2)
                    ranges = parse_ranges(spec)
                    if not ranges:
                        continue
                    total += 1
                    cands = [p for p in files if p == name or p.endswith("/" + name)]
                    where = f"{doc}:{lineno}  `{name}:{spec.strip()}`"
                    if not cands:
                        missing += 1
                        print(f"[基準に無い] {where}")
                        continue
                    ok = [p for p in cands if all(1 <= a <= b <= len(lines_of(p)) for a, b in ranges)]
                    if not ok:
                        bad += 1
                        print(f"[範囲外] {where}  候補: {', '.join(f'{p}({len(lines_of(p))}行)' for p in cands)}")
                        continue
                    if len(cands) > 1:
                        ambiguous += 1
                    if show:
                        print(f"--- {where}" + (f"  (曖昧: {', '.join(cands)})" if len(cands) > 1 else ""))
                        for p in ok[:1]:
                            for a, b in ranges:
                                for i in range(a, b + 1):
                                    print(f"    {p}:{i}: {lines_of(p)[i - 1]}")
    print(f"参照 {total} 件 / 範囲外 {bad} 件 / 基準に無いファイル {missing} 件 / ファイル名が曖昧 {ambiguous} 件（基準: {base}）")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
