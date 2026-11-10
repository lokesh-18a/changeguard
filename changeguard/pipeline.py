"""Orchestrates the five agents: Understand -> Impact -> Design tests -> Run -> Explain.

Each agent has one job and passes a typed result to the next. Only Understand
and Explain call the language model; the rest is deterministic code.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from . import testgen
from .catalog import Catalog, apply_change
from .impact import build_graph, explain_path, find_conflicts, find_impact
from .llm import LLM
from .models import ChangeRequest, Conflict, ImpactItem, TestCase, TestResult
from .runner import run_tests


@dataclass
class RunReport:
    request_text: str
    change: ChangeRequest
    requirements: dict[str, str]
    impact: list[ImpactItem]
    paths: dict[str, str]
    conflicts: list[Conflict]
    tests: list[TestCase]
    results: list[TestResult]
    explanation: str
    trace: list[dict] = field(default_factory=list)
    llm_calls: list = field(default_factory=list)
    backend: str = ""

    @property
    def passed(self) -> int:
        return sum(r.passed for r in self.results)


def catalog_summary(cat: Catalog) -> str:
    return "\n".join(f"{r.id} [{r.area}] {r.name}: params={json.dumps(r.params)}" for r in cat.rules.values())


def run(request_text: str, offline: bool = False, base: Catalog | None = None) -> RunReport:
    base = base or Catalog.load()
    llm = LLM(offline=offline)
    trace = []

    def step(name, fn):
        t0 = time.time()
        out = fn()
        trace.append({"agent": name, "seconds": round(time.time() - t0, 3)})
        return out

    change = step("1 Understand (LLM)", lambda: llm.parse_change(request_text, catalog_summary(base), validate=lambda c: apply_change(base, c)))
    cat, new_ids, changed_ids = apply_change(base, change)
    graph = build_graph(cat)
    impact = step("2 Find impact (code)", lambda: find_impact(cat, new_ids, changed_ids))
    conflicts = step("2b Check conflicts (code)", lambda: find_conflicts(cat, new_ids))
    tests = step("3 Design tests (code)", lambda: testgen.generate(cat, change) + testgen.load_regression(cat))
    impacted = {i.rule_id for i in impact}
    results = step("4 Run tests (code)", lambda: run_tests(cat, tests))

    by_id = {t.id: t for t in tests}
    facts = {
        "change": change.summary,
        "impacted_rules": len(impact),
        "areas": sorted({i.area for i in impact}),
        "conflicts": [c.model_dump() for c in conflicts],
        "passed": sum(r.passed for r in results), "total": len(results),
        "failures": [{"id": r.test_id, "title": by_id[r.test_id].title, "note": r.note} for r in results if not r.passed],
        "stale": [t.id for t in tests if t.stale],
        "regression_tests_touching_impacted_rules": [t.id for t in tests if t.origin == "regression" and impacted & set(t.rules)],
    }
    explanation = step("5 Explain (LLM)", lambda: llm.explain(facts))
    return RunReport(request_text, change, testgen.requirements_for(change), impact,
                     {i.rule_id: explain_path(graph, i.path) for i in impact}, conflicts, tests, results,
                     explanation, trace, [c.__dict__ for c in llm.calls], llm.backend)
