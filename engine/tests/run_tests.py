#!/usr/bin/env python3
"""Test runner with a fast mode and a timing report.

    python run_tests.py                 whole suite
    python run_tests.py --fast          skip the slow end-to-end renders (MH_TEST_FAST=1): about a minute
    python run_tests.py -k color        only tests whose id contains "color"
    python run_tests.py --durations 15  also list the 15 slowest tests
    python run_tests.py --list-slow     show which tests the fast mode skips

Without FFmpeg the end-to-end tests skip themselves, so this also works on a machine that only has Python.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


class TimedResult(unittest.TextTestResult):
    times: dict[str, float] = {}

    def startTest(self, test):
        self._t0 = time.perf_counter()
        super().startTest(test)

    def stopTest(self, test):
        TimedResult.times[test.id()] = time.perf_counter() - self._t0
        super().stopTest(test)


def iter_tests(suite):
    for t in suite:
        if isinstance(t, unittest.TestSuite):
            yield from iter_tests(t)
        else:
            yield t


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fast", action="store_true", help="skip tests marked slow (helpers.slow)")
    ap.add_argument("-k", dest="match", help="run tests whose id contains this text")
    ap.add_argument("--durations", type=int, default=0, metavar="N", help="list the N slowest tests")
    ap.add_argument("--list-slow", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    if args.fast:
        os.environ["MH_TEST_FAST"] = "1"
    sys.path.insert(0, str(HERE))
    suite = unittest.defaultTestLoader.discover(str(HERE), pattern="test_*.py")
    tests = list(iter_tests(suite))
    if args.match:
        tests = [t for t in tests if args.match in t.id()]
    if args.list_slow:
        import helpers
        for t in tests:
            method = getattr(t, t._testMethodName, None)
            if getattr(method, helpers.SLOW_ATTR, False) or getattr(type(t), helpers.SLOW_ATTR, False):
                print(t.id())
        return 0
    flat = unittest.TestSuite(tests)
    runner = unittest.TextTestRunner(verbosity=2 if args.verbose else 1, resultclass=TimedResult)
    t0 = time.perf_counter()
    result = runner.run(flat)
    print(f"\n{result.testsRun} tests in {time.perf_counter() - t0:.0f} s"
          f"{' (fast mode)' if args.fast else ''}; skipped {len(result.skipped)}")
    if args.durations:
        print(f"\nslowest {args.durations}:")
        for tid, sec in sorted(TimedResult.times.items(), key=lambda kv: -kv[1])[:args.durations]:
            print(f"  {sec:6.1f} s  {tid}")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
