#!/usr/bin/env python3
"""One command that mirrors the no-FFmpeg CI job, so a change can be checked locally before it is pushed.

It runs exactly what the `python` job in .github/workflows/mh-video-editor-skill.yml runs: everything compiles, the
CLI reference is up to date, and the fast test suite passes. With FFmpeg installed the full suite and coverage run in
CI as well; pass --full here to run the whole suite locally too.

    python engine/tools/check.py            # compile + CLI doc + fast tests (about two minutes)
    python engine/tools/check.py --full     # the whole suite (needs FFmpeg; the slow renders run)

Exit code 0 means every step passed. The first failing step stops the run and its output is shown.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def _run(label: str, cmd: list[str]) -> bool:
    print(f"==> {label}")
    t0 = time.monotonic()
    r = subprocess.run(cmd, cwd=ROOT)
    dt = time.monotonic() - t0
    print(f"    {'ok' if r.returncode == 0 else 'FAILED'} ({dt:.0f}s)\n")
    return r.returncode == 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--full", action="store_true", help="run the whole test suite (needs FFmpeg) instead of --fast")
    a = ap.parse_args(argv)
    py = sys.executable
    tests = [py, "engine/tests/run_tests.py"] + ([] if a.full else ["--fast"])
    steps = [
        ("compile everything", [py, "-m", "compileall", "-q", "engine/scripts", "engine/tools", "engine/tests"]),
        ("CLI reference is up to date", [py, "engine/tools/gen_cli_doc.py", "--check"]),
        ("tests" + ("" if a.full else " (fast mode)"), tests),
    ]
    for label, cmd in steps:
        if not _run(label, cmd):
            print(f"check failed at: {label}")
            return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
