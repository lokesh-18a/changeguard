"""Runs pipeline A and measures it against the hand-labelled gold set.

  python -m changeguard.knowledge.build                     # rule-based baseline (no AWS)
  python -m changeguard.knowledge.build --extractor nova    # Amazon Nova + verifier + 1 retry
  python -m changeguard.knowledge.build --extractor both    # side-by-side comparison

Writes out/knowledge_report.json and out/extracted_facts.json.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from ..catalog import DATA, Catalog
from ..llm import LLM
from . import extract
from .facts import Fact, norm_value
from .ingest import ingest, manifest, segments
from .reconcile import catalog_check, contradictions, variations
from .verify import verify

EVAL_DOCS = ["raheja_pc", "liberty_pc3y", "usgi_pc", "iffco_nildep", "bajaj_addons"]
CLAUSE_TYPES = {"zero_dep_claim_limit", "zero_dep_condition", "zero_dep_non_authorised_payout"}


def _scope() -> dict:
    return json.loads((DATA / "gold_facts.json").read_text())["scope"]


def _pages(doc: str) -> list[str]:
    pages = ingest(doc)
    scope = _scope().get(doc, "all pages")
    if isinstance(scope, list):
        pages = [p if (i + 1) in scope else "" for i, p in enumerate(pages)]
    return pages


def run(extractor: str = "baseline", docs: list[str] | None = None, llm: LLM | None = None) -> dict:
    docs = docs or EVAL_DOCS
    candidates, accepted, rejected = [], [], []
    texts = {}
    # a document's title counts as context only when the whole document is one cover
    titles = {m["id"]: m["title"] for m in manifest() if m.get("single_cover")}
    for d in docs:
        pages = _pages(d)
        texts[d] = "\n".join(pages)
        if extractor == "baseline":
            cand = extract.baseline(d, pages)
            ok, rej = verify(cand, {d: texts[d]})
        else:
            segs = segments(d, pages)
            cand = extract.nova(llm, segs, title=titles.get(d, ""))
            ok, rej = verify(cand, {d: texts[d]})
            if rej:  # evaluator-optimizer: tell the extractor why, retry those segments once
                fb = defaultdict(list)
                for f, why in rej:
                    if f.seg is not None:
                        fb[f.seg].append(f"{f.type}/{f.key}={f.value}: {why}")
                retry = extract.nova(llm, segs, {k: "; ".join(v) for k, v in fb.items()}, only=set(fb), title=titles.get(d, ""))
                ok2, rej2 = verify(retry, {d: texts[d]})
                known = {f.identity() for f in ok}
                ok += [f for f in ok2 if f.identity() not in known]
                cand += retry
                rej = rej2 + [(f, r) for f, r in rej if f.seg not in fb]
        candidates += cand
        accepted += ok
        rejected += rej
    return {"extractor": extractor, "docs": docs, "candidates": candidates, "accepted": accepted,
            "rejected": rejected, "texts": texts, "calls": (llm.calls if llm else [])}


def score(accepted: list[Fact], docs: list[str]) -> dict:
    gold = json.loads((DATA / "gold_facts.json").read_text())["facts"]
    g = {(f["doc"], f["type"], f["key"], norm_value(f["value"])) for f in gold if f["doc"] in docs}
    p = {f.identity() for f in accepted if f.doc in docs}

    def prf(gs, ps):
        tp = len(gs & ps)
        prec = tp / len(ps) if ps else 0.0
        rec = tp / len(gs) if gs else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        return {"precision": round(prec, 3), "recall": round(rec, 3), "f1": round(f1, 3),
                "true_pos": tp, "predicted": len(ps), "gold": len(gs)}

    split = lambda s, clause: {x for x in s if (x[1] in CLAUSE_TYPES) == clause}
    return {"overall": prf(g, p), "tables": prf(split(g, False), split(p, False)),
            "clauses": prf(split(g, True), split(p, True)),
            "missed": sorted(map(str, g - p))[:40], "extra": sorted(map(str, p - g))[:40]}


def report(res: dict) -> dict:
    acc = res["accepted"]
    out = {
        "extractor": res["extractor"],
        "documents": res["docs"],
        "candidates": len(res["candidates"]),
        "accepted": len(acc),
        "rejected_by_verifier": len(res["rejected"]),
        "rejection_reasons": Counter(r for _, r in res["rejected"]).most_common(8),
        "score_vs_gold": score(acc, res["docs"]),
        "contradictions": contradictions(acc),
        "variations_between_insurers": variations(acc),
        "catalog_check": catalog_check(Catalog.load(), acc),
        "model_calls": len(res["calls"]),
        "tokens": {"input": sum(getattr(c, "input_tokens", 0) for c in res["calls"]),
                   "output": sum(getattr(c, "output_tokens", 0) for c in res["calls"])},
    }
    return out


def _print(r: dict):
    s = r["score_vs_gold"]
    print(f"\n=== {r['extractor'].upper()} extractor on {len(r['documents'])} real documents")
    print(f"candidates {r['candidates']} -> accepted {r['accepted']}, rejected by verifier {r['rejected_by_verifier']}")
    for part in ("overall", "tables", "clauses"):
        m = s[part]
        print(f"  {part:8} precision {m['precision']:.2f}  recall {m['recall']:.2f}  F1 {m['f1']:.2f}  "
              f"({m['true_pos']} correct of {m['predicted']} extracted; gold {m['gold']})")
    if r["rejection_reasons"]:
        print("  top rejection reasons:", r["rejection_reasons"][:4])
    for c in r["contradictions"]:
        print(f"  CONTRADICTION in {c['doc']}: {c['type']} {c['key']} has values {c['values']}")
    for v in r["variations_between_insurers"]:
        print(f"  VARIATION {v['type']} {v['key']}: {v['values']}")
    chk = Counter(x["status"] for x in r["catalog_check"])
    print(f"  catalog check: {dict(chk)}")
    if r["model_calls"]:
        print(f"  model calls {r['model_calls']}, tokens in {r['tokens']['input']} out {r['tokens']['output']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--extractor", choices=["baseline", "nova", "both"], default="baseline")
    ap.add_argument("--out", default="out")
    a = ap.parse_args()
    reports, facts, runs = [], {}, {}
    for ex in (["baseline", "nova"] if a.extractor == "both" else [a.extractor]):
        llm = LLM() if ex == "nova" else None
        if llm and llm.offline:
            print("Nova needs AWS credentials; skipping."); continue
        res = run(ex, llm=llm)
        runs[ex] = res
        rep = report(res)
        _print(rep)
        reports.append(rep)
        facts[ex] = [f.model_dump() for f in res["accepted"]]
    if len(reports) == 2:
        hybrid = [f for f in runs["baseline"]["accepted"] if f.type not in CLAUSE_TYPES] + \
                 [f for f in runs["nova"]["accepted"] if f.type in CLAUSE_TYPES]
        hs = score(hybrid, EVAL_DOCS)
        print("\n=== HYBRID: patterns for tables, Nova for clause wording")
        for part in ("overall", "tables", "clauses"):
            m = hs[part]
            print(f"  {part:8} precision {m['precision']:.2f}  recall {m['recall']:.2f}  F1 {m['f1']:.2f}  "
                  f"({m['true_pos']} correct of {m['predicted']} extracted; gold {m['gold']})")
        reports.append({"extractor": "hybrid", "score_vs_gold": hs})
    out = Path(a.out); out.mkdir(exist_ok=True)
    (out / "knowledge_report.json").write_text(json.dumps(reports, indent=1, default=str))
    (out / "extracted_facts.json").write_text(json.dumps(facts, indent=1, default=str))
    print(f"\nWrote {out / 'knowledge_report.json'} and {out / 'extracted_facts.json'}")


if __name__ == "__main__":
    main()
