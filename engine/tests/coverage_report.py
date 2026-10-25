#!/usr/bin/env python3
"""Line coverage of engine/scripts with the standard library only (needs Python 3.12+: sys.monitoring).

    python coverage_report.py                    run the whole suite, print per-file coverage and the biggest gaps
    python coverage_report.py --pattern "test_color*.py"   run only some test files
    python coverage_report.py --min 80           exit 1 if the total is below 80 %
    python coverage_report.py --json cov.json    also write the numbers
    python coverage_report.py --missing vid_qa   list every uncovered line range of one module

Only code that runs inside this process is measured: commands started as subprocesses (a few CLI tests) are not.
`# pragma: no cover` excludes a line (and the block it opens); `if __name__ == "__main__":` blocks are excluded.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import types
import unittest
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"


def executable_lines(path: Path) -> set[int]:
    source = path.read_text(encoding="utf-8")
    code = compile(source, str(path), "exec")
    lines: set[int] = set()

    def walk(c: types.CodeType):
        for _, _, ln in c.co_lines():
            if ln:
                lines.add(ln)
        for k in c.co_consts:
            if isinstance(k, types.CodeType):
                walk(k)

    walk(code)
    tree = ast.parse(source)
    src_lines = source.splitlines()
    drop: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare) and isinstance(node.test.left, ast.Name) \
                and node.test.left.id == "__name__":
            drop.update(range(node.lineno, node.end_lineno + 1))
        ln = getattr(node, "lineno", None)
        if ln and re.search(r"#\s*pragma:\s*no cover", src_lines[ln - 1]):
            drop.update(range(ln, getattr(node, "end_lineno", ln) + 1))
    return lines - drop


def ranges(nums: list[int]) -> str:
    out, start, prev = [], None, None
    for n in sorted(nums):
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            out.append((start, prev))
            start = prev = n
    if start is not None:
        out.append((start, prev))
    return ", ".join(f"{a}" if a == b else f"{a}-{b}" for a, b in out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pattern", default="test_*.py")
    ap.add_argument("--min", type=float, default=0.0, help="fail when total coverage is below this percentage")
    ap.add_argument("--json")
    ap.add_argument("--missing", help="module name (e.g. vid_qa): print every uncovered line range")
    args = ap.parse_args()
    if not hasattr(sys, "monitoring"):
        print("coverage_report.py needs Python 3.12 or newer (sys.monitoring); the test suite itself runs on 3.9+", file=sys.stderr)
        return 2

    wanted = {str(p): p for p in SCRIPTS.glob("*.py")}
    hits: dict[str, set[int]] = defaultdict(set)
    mon = sys.monitoring
    tool = mon.COVERAGE_ID
    mon.use_tool_id(tool, "mh-coverage")

    def on_line(code, line):
        f = code.co_filename
        if f in wanted:
            hits[f].add(line)
        return mon.DISABLE                                   # each (code, line) only needs to be seen once

    mon.register_callback(tool, mon.events.LINE, on_line)
    mon.set_events(tool, mon.events.LINE)
    sys.path.insert(0, str(HERE))
    sys.path.insert(0, str(SCRIPTS))
    try:
        suite = unittest.defaultTestLoader.discover(str(HERE), pattern=args.pattern)
        result = unittest.TextTestRunner(verbosity=0, stream=sys.stderr).run(suite)
    finally:
        mon.set_events(tool, 0)
        mon.free_tool_id(tool)

    rows, tot_exec, tot_hit = [], 0, 0
    report = {}
    for f, p in sorted(wanted.items(), key=lambda kv: kv[1].name):
        ex = executable_lines(p)
        hit = hits.get(f, set()) & ex
        miss = sorted(ex - hit)
        pct = 100.0 * len(hit) / len(ex) if ex else 100.0
        rows.append((p.stem, len(ex), len(miss), pct, miss))
        tot_exec += len(ex)
        tot_hit += len(hit)
        report[p.stem] = {"lines": len(ex), "missed": len(miss), "percent": round(pct, 1), "missing": ranges(miss)}
    total = 100.0 * tot_hit / tot_exec if tot_exec else 100.0
    print(f"\n{'module':<14}{'lines':>7}{'missed':>8}{'cover':>8}")
    for name, n, m, pct, _ in sorted(rows, key=lambda r: r[3]):
        print(f"{name:<14}{n:>7}{m:>8}{pct:>7.1f}%")
    print(f"{'TOTAL':<14}{tot_exec:>7}{tot_exec - tot_hit:>8}{total:>7.1f}%")
    print("\nlargest gaps (module: uncovered lines):")
    for name, n, m, pct, miss in sorted(rows, key=lambda r: -r[2])[:5]:
        print(f"  {name}: {ranges(miss)[:200]}")
    if args.missing:
        row = next((r for r in rows if r[0] == args.missing), None)
        print(f"\n{args.missing}: {ranges(row[4]) if row else 'unknown module'}")
    if args.json:
        Path(args.json).write_text(json.dumps({"total": round(total, 1), "modules": report}, indent=2), encoding="utf-8")
    if not result.wasSuccessful():
        return 1
    return 1 if total < args.min else 0


if __name__ == "__main__":
    sys.exit(main())
