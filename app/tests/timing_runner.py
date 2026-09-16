#!/usr/bin/env python3
"""Per-case timing runner for the verification gate (v30.3).

Runs the given unittest modules exactly like `python -m unittest mod -v`
but additionally measures wall-clock time per test case and prints a
machine-readable JSON payload to stdout behind a sentinel line.

stdout : ###TIMING_JSON###{"ok": true, "ran": 45, "cases": [...]}
stderr : the normal unittest -v progress output (human-readable)
exit   : 0 when everything passed, 1 otherwise

Usage:
    python3 tests/timing_runner.py tests.test_core tests.test_e2e
"""

import io
import json
import os
import sys
import time
import unittest


class TimingResult(unittest.TextTestResult):
    """Records per-case wall-clock duration and status alongside the
    standard verbose output."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._cases = {}
        self._t0 = 0.0
        self._current = None

    # -- timing bookkeeping ------------------------------------------------
    def startTest(self, test):
        super().startTest(test)
        self._current = test.id()
        self._t0 = time.perf_counter()

    def _record(self, status):
        if self._current is None:
            return
        ms = (time.perf_counter() - self._t0) * 1000.0
        # keep the FIRST status if a case reports twice (subTest edge)
        self._cases.setdefault(
            self._current, {"id": self._current, "status": status, "ms": round(ms, 1)}
        )

    # -- status hooks -------------------------------------------------------
    def addSuccess(self, test):
        self._record("ok")
        super().addSuccess(test)

    def addFailure(self, test, err):
        self._record("FAIL")
        super().addFailure(test, err)

    def addError(self, test, err):
        self._record("ERROR")
        super().addError(test, err)

    def addSkip(self, test, reason):
        self._record("skipped")
        super().addSkip(test, reason)

    def addSubTest(self, test, subtest, err):
        # subTest failures fold into the parent case's status
        if err is not None and self._current in self._cases:
            self._cases[self._current]["status"] = "FAIL"
        super().addSubTest(test, subtest, err)

    @property
    def cases(self):
        return list(self._cases.values())


def main() -> int:
    modules = sys.argv[1:]
    if not modules:
        print("usage: timing_runner.py <unittest-module> [...]", file=sys.stderr)
        return 2

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for mod in modules:
        suite.addTests(loader.loadTestsFromName(mod))

    stream = io.StringIO()
    runner = unittest.TextTestRunner(
        stream=stream, resultclass=TimingResult, verbosity=2
    )
    result = runner.run(suite)

    payload = {
        "ok": result.wasSuccessful(),
        "ran": result.testsRun,
        "cases": result.cases,
    }
    print("###TIMING_JSON###" + json.dumps(payload))
    # human-readable verbose output goes to stderr like unittest itself
    print(stream.getvalue(), file=sys.stderr, end="")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    # allow `python3 tests/timing_runner.py …` from the repo root even
    # though tests/ is a namespace package (script dir ≠ cwd)
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    raise SystemExit(main())
