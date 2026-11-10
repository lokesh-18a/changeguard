"""Benchmark for the change pipeline, on 12 hand-labelled change requests.

Measures
  understanding  is the plain-English request turned into the right structured change?
                 (exact match on every key field), plus pass^k: the share of requests
                 understood correctly in ALL k repeated runs (the tau-bench reliability metric)
  impact         precision and recall of the impacted rules against an analyst's labels,
                 for the dependency graph alone and for graph + conflict checks

  python -m changeguard.benchmark --offline        # rule-based parser (no AWS)
  python -m changeguard.benchmark --k 3            # Amazon Nova, 3 runs per request
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .catalog import DATA, Catalog, apply_change
from .impact import find_conflicts, find_impact
from .llm import LLM, _normalise, _offline_parse
from .models import ChangeRequest
from .pipeline import catalog_summary

ADDON_FIELDS = ["max_car_age_years", "price_pct_of_od", "claim_limit_per_year", "allow_midterm"]
PARAM_FIELDS = ["rule_id", "param_path", "new_value"]


def load() -> list[dict]:
    return json.loads((DATA / "benchmark_changes.json").read_text())["changes"]


def _eq(a, b) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool):
        return abs(float(a) - float(b)) < 1e-6
    return a == b


def field_errors(pred: ChangeRequest | None, gold: dict) -> list[str]:
    if pred is None:
        return ["no valid structured change"]
    if pred.kind != gold["kind"]:
        return [f"kind: got {pred.kind}, expected {gold['kind']}"]
    fields = ADDON_FIELDS if gold["kind"] == "new_addon" else PARAM_FIELDS
    return [f"{f}: got {getattr(pred, f)!r}, expected {gold[f]!r}" for f in fields if not _eq(getattr(pred, f), gold[f])]


def parse_once(text: str, llm: LLM | None, base: Catalog) -> ChangeRequest | None:
    try:
        if llm is None:
            cr = _normalise(_offline_parse(text))
            apply_change(base, cr)
            return cr
        return llm._bedrock_parse(text, catalog_summary(base), validate=lambda c: apply_change(base, c))
    except Exception:
        return None


def _gold_change(item: dict) -> ChangeRequest:
    return ChangeRequest(summary=item["text"], **item["gold"])


def _rename(rules: set[str], code_from: str | None, code_to: str | None) -> set[str]:
    if not code_from or not code_to or code_from == code_to:
        return rules
    return {r.replace(f"{code_from}-", f"{code_to}-", 1) if r.startswith(f"{code_from}-") else r for r in rules}


def impact_sets(change: ChangeRequest, base: Catalog) -> tuple[set[str], set[str]]:
    cat, new_ids, changed = apply_change(base, change)
    graph = {i.rule_id for i in find_impact(cat, new_ids, changed)}
    conflicts = {r for c in find_conflicts(cat, new_ids, changed) for r in c.rules}
    return graph, graph | conflicts


def prf(pred: set, gold: set) -> tuple[float, float]:
    tp = len(pred & gold)
    return (tp / len(pred) if pred else 0.0), (tp / len(gold) if gold else 0.0)


def run(k: int = 1, offline: bool = False) -> dict:
    base = Catalog.load()
    llm = None if offline else LLM()
    if llm is not None and llm.offline:
        raise SystemExit("Nova needs AWS credentials (or use --offline).")
    rows = []
    for item in load():
        gold = item["gold"]
        runs = [parse_once(item["text"], llm, base) for _ in range(k)]
        errs = [field_errors(p, gold) for p in runs]
        # impact quality, isolated from parsing: use the gold structured change
        g_graph, g_full = impact_sets(_gold_change(item), base)
        gold_imp = set(item["gold_impact"])
        p1, r1 = prf(g_graph, gold_imp)
        p2, r2 = prf(g_full, gold_imp)
        rows.append({"id": item["id"], "kind": gold["kind"], "runs_correct": sum(not e for e in errs), "k": k,
                     "errors": [e for e in errs if e][:2],
                     "graph_precision": round(p1, 3), "graph_recall": round(r1, 3),
                     "full_precision": round(p2, 3), "full_recall": round(r2, 3),
                     "missed": sorted(gold_imp - g_full), "extra": sorted(g_full - gold_imp)})
    n = len(rows)
    summary = {
        "parser": "rule-based (offline)" if offline else "Amazon Nova",
        "requests": n, "k": k,
        "understanding_accuracy": round(sum(r["runs_correct"] for r in rows) / (n * k), 3),
        "pass_hat_k": round(sum(r["runs_correct"] == k for r in rows) / n, 3),
        "impact_graph_precision": round(sum(r["graph_precision"] for r in rows) / n, 3),
        "impact_graph_recall": round(sum(r["graph_recall"] for r in rows) / n, 3),
        "impact_full_precision": round(sum(r["full_precision"] for r in rows) / n, 3),
        "impact_full_recall": round(sum(r["full_recall"] for r in rows) / n, 3),
        "tokens_in": sum(c.input_tokens for c in (llm.calls if llm else [])),
        "tokens_out": sum(c.output_tokens for c in (llm.calls if llm else [])),
    }
    return {"summary": summary, "rows": rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=1)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--out", default="out")
    a = ap.parse_args()
    res = run(a.k, a.offline)
    s = res["summary"]
    print(f"\n=== Change pipeline benchmark: {s['requests']} labelled requests, parser = {s['parser']}, k = {s['k']}")
    print(f"understanding accuracy {s['understanding_accuracy']:.0%}   pass^{s['k']} {s['pass_hat_k']:.0%}")
    print(f"impact (graph only)        precision {s['impact_graph_precision']:.0%}  recall {s['impact_graph_recall']:.0%}")
    print(f"impact (graph + conflicts) precision {s['impact_full_precision']:.0%}  recall {s['impact_full_recall']:.0%}")
    if s["tokens_in"]:
        print(f"tokens in {s['tokens_in']} out {s['tokens_out']}")
    for r in res["rows"]:
        mark = "OK " if r["runs_correct"] == r["k"] else "ERR"
        print(f"  {r['id']} {mark} {r['runs_correct']}/{r['k']}  recall {r['full_recall']:.0%}  missed {r['missed']}  "
              f"{r['errors'][0] if r['errors'] else ''}")
    out = Path(a.out); out.mkdir(exist_ok=True)
    (out / f"benchmark_{'offline' if a.offline else 'nova'}.json").write_text(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
