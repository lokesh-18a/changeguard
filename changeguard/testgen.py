"""Test design, driven entirely by the rule catalog.

Every rule can declare how tests exercise it (its `exercise` list): which parameter holds
a limit, which input moves across that limit, and which operation (quote, mid-term
charge, claim, renewal) shows the effect. From that, the designer derives tests with
standard techniques, for whichever rules a change touches:

  boundary value     just below, at, and just above every limit or band edge
  equivalence class  one typical case per rule
  exclusion          one case per excluded category
  range              first, middle and last value of a range
  rounding probe     a typical case with irregular amounts, so rounding slips show

Expected results always come from the oracle, never from the language model.
Nothing here is specific to one add-on: a new rule with an `exercise` entry gets tests.
"""
from __future__ import annotations

import json

from . import oracle
from .catalog import DATA, Catalog, addon_code
from .models import ChangeRequest, ImpactItem, TestCase

BASE_CAR = {"ex_showroom_price": 800_000, "engine_cc": 1200, "claim_free_years": 1}
DAMAGE = [{"name": "front bumper", "type": "plastic", "cost": 10_000},
          {"name": "left door", "type": "metal", "cost": 20_000}]

COMPARE = {
    "quote": ["total_premium", "rejected_addons", "addon_premiums"],
    "midterm": ["accepted", "charge"],
    "claim": ["payout", "depreciation", "total_loss"],
    "renewal": ["addon_retained", "ncb_pct"],
}


def requirements_for(cat: Catalog, new_ids: list[str], changed_ids: list[str]) -> dict[str, str]:
    """One requirement per new or changed rule, in the rule's own words."""
    return {rid: cat.get(rid).description for rid in list(new_ids) + list(changed_ids)}


def _template(op: str, addon: str | None) -> dict:
    t = {"quote": {**BASE_CAR, "car_age": 2.0, "addons": [addon] if addon else []},
         "midterm": {**BASE_CAR, "car_age": 2.0, "addon": addon, "month_added": 7},
         "claim": {**BASE_CAR, "car_age": 3.5, "cause": "accident", "claim_number": 1, "parts": DAMAGE},
         "renewal": {**BASE_CAR, "car_age": 3.0, "had_claim": False}}[op]
    if addon and op in ("claim", "renewal"):
        t["addon"] = addon
    return t


def _round(x, step):
    return round(x, 2) if isinstance(step, float) else int(x)


def _points(cat: Catalog, rule, spec) -> list[tuple[object, str, str]]:
    """(value, technique, label) for one exercise spec."""
    step = spec.get("step", 1)
    off = spec.get("offset", 0)
    if spec.get("typical"):
        return [(None, "equivalence class", "typical case"), (None, "rounding probe", "irregular amounts")]
    if "values" in spec:
        return [(v, "equivalence class", f"{spec['input']} = {v}") for v in spec["values"]]
    if spec.get("categorical"):
        return [(c, "exclusion", f"excluded: {c}") for c in rule.params[spec["param"]]]
    if spec.get("range"):
        lo, hi = rule.params[spec["param"]]
        return [(lo, "boundary value", "first"), ((lo + hi) // 2 + 1, "equivalence class", "middle"), (hi, "boundary value", "last")]
    raw = rule.params[spec["param"]]
    if spec.get("via"):                                   # band names resolved through another rule
        vrule, vparam = spec["via"].split(".")
        raw = cat.param(vrule, vparam)
    pts = []
    if isinstance(raw, list) and raw and isinstance(raw[0], list):        # [[upper, value], ...]
        for upper, _ in raw[:-1]:
            pts += [(_round(upper + off, step), "boundary value", f"at band edge {upper}"),
                    (_round(upper + step + off, step), "boundary value", f"just above {upper}")]
    elif isinstance(raw, list):                                           # slab indexed by count
        pts += [(i, "boundary value", f"{spec['input']} = {i}") for i in range(len(raw) + 1)]
    elif isinstance(raw, (int, float)):                                   # a single limit
        pts += [(_round(raw - step + off, step), "boundary value", f"just below limit {raw:g}"),
                (_round(raw + off, step), "boundary value", f"at limit {raw:g}"),
                (_round(raw + step + off, step), "boundary value", f"just above limit {raw:g}")]
    return pts


def generate(cat: Catalog, change: ChangeRequest, impact: list[ImpactItem]) -> list[TestCase]:
    addon = addon_code(change) if change.kind == "new_addon" else None
    touched = [i.rule_id for i in impact]
    tests: list[TestCase] = []
    seen: set[str] = set()
    for rid in touched:
        rule = cat.get(rid)
        req = rid
        for spec in rule.exercise:
            for op in spec["ops"]:
                if op == "midterm" and not addon:
                    continue
                for value, technique, label in _points(cat, rule, spec):
                    inputs = _template(op, addon)
                    if technique == "rounding probe":   # amounts that leave fractions, so rounding slips show
                        inputs["ex_showroom_price"] = 812_347
                        if "parts" in inputs:
                            inputs["parts"] = [{**p, "cost": p["cost"] + 3 + 4 * i} for i, p in enumerate(inputs["parts"])]
                    if value is not None:
                        inputs[spec["input"]] = value
                    key = op + json.dumps(inputs, sort_keys=True)
                    if key in seen:
                        continue
                    seen.add(key)
                    exp = oracle.expected(cat, op, inputs)
                    tests.append(TestCase(
                        id=f"GT-{len(tests) + 1:02d}", kind=op, technique=technique, requirement=req, rules=[rid],
                        title=f"{rid} via {op}: {label}" + (f" ({spec['input']} = {value})" if value is not None and label.startswith(("at", "just", "first", "last", "middle")) else ""),
                        inputs=inputs, expected={k: exp[k] for k in COMPARE[op] if k in exp}))
    return tests


def i_status(impact: list[ImpactItem], rid: str) -> str:
    return next((i.status for i in impact if i.rule_id == rid), "")


def _subset(result: dict, kind: str) -> dict:
    return {k: result[k] for k in COMPARE[kind] if k in result}


def load_regression(cat_now: Catalog) -> list[TestCase]:
    """Loads the existing regression suite and re-computes each expected value
    under the changed rules. A test whose stored answer no longer matches is stale."""
    raw = json.loads((DATA / "regression_tests.json").read_text())
    tests = []
    for t in raw["tests"]:
        tc = TestCase(**t, origin="regression")
        new_exp = _subset(oracle.expected(cat_now, tc.kind, tc.inputs), tc.kind)
        if new_exp != tc.expected:
            tc.stale, tc.old_expected, tc.expected = True, tc.expected, new_exp
        tests.append(tc)
    return tests
