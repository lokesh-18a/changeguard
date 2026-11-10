"""Orchestrates the change pipeline (pipeline B):

  Planner (LLM) -> Impact analyst -> Conflict checker -> Test designer -> Oracle + runner
  -> Mutation auditor -> Writer (LLM) -> Critic (code)

Each agent has one job and passes a typed record to the next. Only the Planner and the
Writer call the language model; everything that decides pass or fail is plain code.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from . import mutation, testgen
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
    mutation: dict = field(default_factory=dict)
    critic: dict = field(default_factory=dict)
    trace: list[dict] = field(default_factory=list)
    llm_calls: list = field(default_factory=list)
    backend: str = ""

    @property
    def passed(self) -> int:
        return sum(r.passed for r in self.results)


def catalog_summary(cat: Catalog) -> str:
    return "\n".join(f"{r.id} [{r.area}] {r.name}: params={json.dumps(r.params)}" for r in cat.rules.values())


def run(request_text: str, offline: bool = False, base: Catalog | None = None,
        mutants: int | None = None, seed: int = 7) -> RunReport:
    base = base or Catalog.load()
    llm = LLM(offline=offline)
    trace = []

    def step(name, fn):
        t0 = time.time()
        out = fn()
        trace.append({"agent": name, "seconds": round(time.time() - t0, 3)})
        return out

    change = step("Planner (LLM)", lambda: llm.parse_change(
        request_text, catalog_summary(base), validate=lambda c: apply_change(base, c)))
    cat, new_ids, changed_ids = apply_change(base, change)
    graph = build_graph(cat)
    impact = step("Impact analyst", lambda: find_impact(cat, new_ids, changed_ids))
    conflicts = step("Conflict checker", lambda: find_conflicts(cat, new_ids, changed_ids))
    tests = step("Test designer", lambda: testgen.generate(cat, change, impact) + testgen.load_regression(cat))
    results = step("Oracle + runner", lambda: run_tests(cat, tests))

    mut = {}
    if mutants != 0:   # None = every possible mutant; N = a random sample of N
        def _audit():
            sample = mutation.sample(mutants, seed)
            full = mutation.audit(cat, tests, sample, "ChangeGuard suite")
            base_suite = mutation.baseline_suite(tests)
            basic = mutation.audit(cat, base_suite, sample, "typical-case suite")
            return {"mutants": len(sample), "seed": seed, "total_points": len(mutation.all_mutants()),
                    "score": round(full.score, 3), "killed": full.killed, "tests": len(tests),
                    "baseline_score": round(basic.score, 3), "baseline_killed": basic.killed,
                    "baseline_tests": len(base_suite), "survivors": full.survivors,
                    "only_ours": sorted(set(full.killers) - set(basic.killers)),
                    "descriptions": {m.id: m.description for m in sample},
                    "kinds": {m.id: m.kind for m in sample},
                    "killers": full.killers, "baseline_killers": basic.killers}
        mut = step("Mutation auditor", _audit)

    impacted = {i.rule_id for i in impact}
    by_id = {t.id: t for t in tests}
    facts = {
        "change": change.summary,
        "impacted_rules": len(impact),
        "rule_ids": sorted(impacted),
        "areas": sorted({i.area for i in impact}),
        "conflicts": [c.model_dump() for c in conflicts],
        "passed": sum(r.passed for r in results), "total": len(results),
        "failures": [{"id": r.test_id, "title": by_id[r.test_id].title, "note": r.note} for r in results if not r.passed],
        "stale": [t.id for t in tests if t.stale],
        "mutation_score": mut.get("score"), "baseline_mutation_score": mut.get("baseline_score"),
        "mutants": mut.get("mutants"),
    }
    from .critic import review  # local import keeps the module graph simple
    explanation, critic = step("Writer (LLM) + Critic", lambda: review(llm, facts, tests, cat))
    return RunReport(request_text, change, testgen.requirements_for(cat, new_ids, changed_ids), impact,
                     {i.rule_id: explain_path(graph, i.path) for i in impact}, conflicts, tests, results,
                     explanation, mut, critic, trace, [c.__dict__ for c in llm.calls], llm.backend)
