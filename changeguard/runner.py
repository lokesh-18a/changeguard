"""Runs test cases against the system under test and compares with the oracle."""
from __future__ import annotations

from . import engine
from .catalog import Catalog
from .models import TestCase, TestResult
from .testgen import COMPARE


def run_tests(cat: Catalog, tests: list[TestCase]) -> list[TestResult]:
    results = []
    for t in tests:
        try:
            raw = engine.run(cat, t.kind, t.inputs)
            actual = {k: raw[k] for k in COMPARE[t.kind] if k in raw}
            diffs = [k for k in t.expected if t.expected[k] != actual.get(k)]
            note = "" if not diffs else "Mismatch in " + ", ".join(
                f"{k}: expected {t.expected[k]}, got {actual.get(k)}" for k in diffs)
            results.append(TestResult(test_id=t.id, passed=not diffs, expected=t.expected, actual=actual, note=note))
        except Exception as exc:  # a crash is a failure, not a stop
            results.append(TestResult(test_id=t.id, passed=False, expected=t.expected, actual=None, note=f"Error: {exc}"))
    return results
