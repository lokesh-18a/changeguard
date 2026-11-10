"""Reconciler: compares verified facts within and across documents, and checks the
rule catalog against them.

  contradiction  one document gives two different values for the same key
  variation      insurers differ on the same key (normal, but the catalog must pick one)
  catalog check  each catalog parameter that has a real-world counterpart is compared
                 with what the documents say
"""
from __future__ import annotations

from collections import defaultdict

from ..catalog import Catalog
from .facts import AGE_BANDS, Fact, norm_value


def contradictions(facts: list[Fact]) -> list[dict]:
    by = defaultdict(set)
    quotes = defaultdict(list)
    for f in facts:
        by[(f.doc, f.type, f.key)].add(norm_value(f.value))
        quotes[(f.doc, f.type, f.key)].append(f.quote)
    return [{"doc": d, "type": t, "key": k, "values": sorted(map(str, v)), "quotes": quotes[(d, t, k)]}
            for (d, t, k), v in by.items() if len(v) > 1]


def variations(facts: list[Fact]) -> list[dict]:
    by = defaultdict(lambda: defaultdict(set))
    for f in facts:
        by[(f.type, f.key)][str(norm_value(f.value))].add(f.doc)
    return [{"type": t, "key": k, "values": {v: sorted(d) for v, d in vals.items()}}
            for (t, k), vals in sorted(by.items()) if len(vals) > 1]


def _consensus(facts: list[Fact], ftype: str, key: str):
    counts = defaultdict(set)
    for f in facts:
        if f.type == ftype and f.key == key:
            counts[norm_value(f.value)].add(f.doc)
    if not counts:
        return None, []
    v, docs = max(counts.items(), key=lambda kv: len(kv[1]))
    return v, sorted(docs)


def catalog_check(cat: Catalog, facts: list[Fact]) -> list[dict]:
    """Compares the catalog's numbers with the real documents. Each row says whether the
    catalog value is confirmed, contradicted, or has no source."""
    rows = []
    upper = {"0-0.5": 0.5, "0.5-1": 1, "1-2": 2, "2-3": 3, "3-4": 4, "4-5": 5, "5-10": 10, "10+": 99}

    def row(rule, param, label, ours, ftype, key, scale=100):
        real, docs = _consensus(facts, ftype, key)
        status = "no source" if real is None else ("confirmed" if abs(float(real) - ours * scale) < 1e-9 else "differs")
        rows.append({"rule": rule, "parameter": f"{param} {label}", "catalog": round(ours * scale, 4),
                     "documents": real, "status": status, "sources": docs})

    for band in AGE_BANDS:
        u = upper[band]
        metal = dict((a, b) for a, b in cat.param("CLM-DEP-PARTS", "metal_by_age"))
        if u in metal:
            row("CLM-DEP-PARTS", "metal_by_age", band, metal[u], "metal_depreciation", band)
        idv = dict((a, b) for a, b in cat.param("RAT-IDV", "depreciation_by_age"))
        if u in idv and band in AGE_BANDS[:6]:
            row("RAT-IDV", "depreciation_by_age", band, idv[u], "idv_depreciation", band)
    fixed = cat.param("CLM-DEP-PARTS", "fixed_by_type")
    for ours_key, key in (("plastic", "rubber_nylon_plastic"), ("fibreglass", "fibreglass"), ("glass", "glass")):
        row("CLM-DEP-PARTS", "fixed_by_type", ours_key, fixed[ours_key], "parts_depreciation", key)
    slab = cat.param("RAT-NCB", "slab")
    for years in range(1, 6):
        row("RAT-NCB", "slab", f"{years} yr", slab[years], "ncb", str(years))
    row("CLM-TOTAL-LOSS", "threshold", "", cat.param("CLM-TOTAL-LOSS", "threshold"), "total_loss_threshold", "pct_of_idv")
    return rows
