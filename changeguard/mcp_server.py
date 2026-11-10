"""MCP server: exposes ChangeGuard's deterministic tools to any MCP-capable agent
(Claude Desktop, VS Code agents, or Flarre-style orchestrators).

Run (stdio):   python -m changeguard.mcp_server
Run (HTTP):    python -m changeguard.mcp_server --http      -> http://localhost:8000/mcp

Tools that take a structured change are pure code: same input, same answer.
Only analyze_request calls the language model.
"""
from __future__ import annotations

import json
import sys

from mcp.server.mcpserver import MCPServer

from . import mutation, testgen
from .catalog import Catalog, apply_change
from .impact import build_graph, explain_path, find_conflicts, find_impact
from .models import ChangeRequest
from .pipeline import run
from .runner import run_tests

server = MCPServer(
    name="changeguard",
    instructions=("Insurance change-impact and test tools. Call list_rules to see the product, "
                  "impact_of_change for affected rules and conflicts, generate_tests and run_change "
                  "for verified test results. Expected results come from a deterministic oracle."),
)


def _apply(change: ChangeRequest):
    base = Catalog.load()
    return apply_change(base, change)


def _tests(change: ChangeRequest):
    cat, new_ids, changed = _apply(change)
    impact = find_impact(cat, new_ids, changed)
    return cat, testgen.generate(cat, change, impact) + testgen.load_regression(cat)


@server.tool(description="List the product's business rules, optionally for one area "
                         "(vehicle, rating, underwriting, billing, claims, renewal, documents).")
def list_rules(area: str = "") -> list[dict]:
    cat = Catalog.load()
    return [{"id": r.id, "area": r.area, "name": r.name, "description": r.description,
             "inputs": r.inputs, "outputs": r.outputs, "params": r.params}
            for r in cat.rules.values() if not area or r.area == area]


@server.tool(description="Every rule a structured change affects, with the dependency path that explains why, "
                         "plus conflicts with existing rules.")
def impact_of_change(change: ChangeRequest) -> dict:
    cat, new_ids, changed = _apply(change)
    g = build_graph(cat)
    impact = find_impact(cat, new_ids, changed)
    return {"impact": [{**i.model_dump(exclude={"path"}), "why": explain_path(g, i.path)} for i in impact],
            "conflicts": [c.model_dump() for c in find_conflicts(cat, new_ids, changed)]}


@server.tool(description="Generate test cases for a structured change (boundary values, equivalence classes, "
                         "exclusions) with expected results computed by the oracle; also flags stale regression tests.")
def generate_tests(change: ChangeRequest) -> list[dict]:
    _, tests = _tests(change)
    return [t.model_dump() for t in tests]


@server.tool(description="Apply a structured change, generate the tests and run them against the system. "
                         "Returns pass/fail per test.")
def run_change(change: ChangeRequest) -> dict:
    cat, tests = _tests(change)
    results = run_tests(cat, tests)
    by = {t.id: t for t in tests}
    return {"passed": sum(r.passed for r in results), "total": len(results),
            "failures": [{"id": r.test_id, "title": by[r.test_id].title, "note": r.note} for r in results if not r.passed],
            "stale_regression_tests": [t.id for t in tests if t.stale]}


@server.tool(description="Measure test strength: inject random small defects (mutants) into the system under test "
                         "and report how many the generated tests catch, against a typical-case suite.")
def mutation_audit(change: ChangeRequest, sample: int = 0, seed: int = 7) -> dict:
    cat, tests = _tests(change)
    muts = mutation.sample(sample or None, seed)
    ours = mutation.audit(cat, tests, muts)
    base = mutation.audit(cat, mutation.baseline_suite(tests), muts)
    return {"mutants": len(muts), "score": round(ours.score, 3), "baseline_score": round(base.score, 3),
            "survivors": ours.survivors}


@server.tool(description="Full pipeline from a plain-English change request (uses Amazon Bedrock to understand "
                         "and explain; everything else is deterministic).")
def analyze_request(request: str) -> dict:
    rep = run(request)
    return {"understood_as": rep.change.model_dump(exclude_none=True), "rules_affected": len(rep.impact),
            "conflicts": [c.model_dump() for c in rep.conflicts], "passed": rep.passed,
            "total": len(rep.results), "report": rep.explanation, "ai_calls": rep.llm_calls}


@server.resource("rules://catalog", name="rule-catalog", description="The full rule catalog as JSON",
                 mime_type="application/json")
def catalog_resource() -> str:
    cat = Catalog.load()
    return json.dumps({"product": cat.product, "rules": [r.model_dump() for r in cat.rules.values()]}, indent=1)


if __name__ == "__main__":
    server.run("streamable-http" if "--http" in sys.argv else "stdio")
