"""Test design: turns a change into concrete test cases, deterministically.

Techniques: boundary values (just below, at, just above each limit),
equivalence classes (one typical case per group), exclusions, and
re-checking the stored regression suite for tests the change made stale.
Expected results always come from the oracle, never from the language model.
"""
from __future__ import annotations

import json

from . import oracle
from .catalog import DATA, Catalog
from .models import ChangeRequest, TestCase

BASE_CAR = {"ex_showroom_price": 800_000, "engine_cc": 1200, "claim_free_years": 1}
DAMAGE = [{"name": "front bumper", "type": "plastic", "cost": 10_000},
          {"name": "left door", "type": "metal", "cost": 20_000}]

COMPARE = {
    "quote": ["total_premium", "rejected_addons", "addon_premiums"],
    "midterm": ["accepted", "charge"],
    "claim": ["payout", "depreciation", "total_loss"],
    "renewal": ["addon_retained", "ncb_pct"],
}


def requirements_for(change: ChangeRequest) -> dict[str, str]:
    if change.kind != "new_addon":
        return {"R1": change.summary}
    reqs = {
        "R1": f"Available only for cars up to {change.max_car_age_years:g} years old at policy start.",
        "R2": f"Priced at {change.price_pct_of_od:.0%} of the own-damage premium.",
        "R3": f"No depreciation deducted for up to {change.claim_limit_per_year} claims per policy year.",
        "R4": f"Not applicable to: {', '.join(change.excluded_causes)}.",
        "R6": "Dropped at renewal once the car is older than the age limit.",
    }
    if change.allow_midterm:
        reqs["R5"] = "Can be added mid-term, charged only for the remaining months."
    return dict(sorted(reqs.items()))


def _subset(result: dict, kind: str) -> dict:
    return {k: result[k] for k in COMPARE[kind] if k in result}


def generate(cat: Catalog, change: ChangeRequest) -> list[TestCase]:
    if change.kind != "new_addon":
        return []
    code = (change.addon_code or "ADDON").upper()
    max_age = change.max_car_age_years
    limit = change.claim_limit_per_year
    reqs = requirements_for(change)
    tests: list[TestCase] = []

    def add(title, kind, technique, req, rules, inputs):
        exp = _subset(oracle.expected(cat, kind, inputs), kind)
        tests.append(TestCase(id=f"GT-{len(tests) + 1:02d}", title=title, kind=kind, technique=technique,
                              requirement=req, rules=rules, inputs=inputs, expected=exp))

    # R1 eligibility: boundary values around the age limit
    for age, label in [(round(max_age - 0.1, 2), "just below"), (max_age, "exactly at"), (round(max_age + 0.1, 2), "just above")]:
        add(f"Quote with {code}, car {label} the {max_age:g}-year limit ({age} yrs)", "quote", "boundary value",
            "R1", [f"{code}-ELIG", f"{code}-PRICE"], {**BASE_CAR, "car_age": age, "addons": [code]})
    # R1 / R2 equivalence classes
    add(f"Quote with {code}, typical 2-year-old car", "quote", "equivalence class", "R2",
        [f"{code}-PRICE", "RAT-TOTAL", "RAT-GST"], {**BASE_CAR, "car_age": 2.0, "addons": [code]})
    add(f"Quote with {code}, old car (9 yrs) is refused", "quote", "equivalence class", "R1",
        [f"{code}-ELIG"], {**BASE_CAR, "car_age": 9.0, "addons": [code]})
    add(f"Quote with {code} and maximum NCB (5 claim-free years)", "quote", "equivalence class", "R2",
        [f"{code}-PRICE", "RAT-NCB", "RAT-OD-PREM"], {**BASE_CAR, "car_age": 2.0, "claim_free_years": 5, "addons": [code]})

    # R5 mid-term addition: first, middle, last month
    if change.allow_midterm:
        for m in (1, 7, 12):
            add(f"Add {code} mid-term in month {m}", "midterm", "boundary value" if m in (1, 12) else "equivalence class",
                "R5", [f"{code}-MIDTERM", "BIL-ENDORSE"], {**BASE_CAR, "car_age": 2.0, "addon": code, "month_added": m})

    # R3 claim limit: boundary on claim count
    for n in (1, limit, limit + 1):
        add(f"Claim number {n} with {code} (limit {limit})", "claim", "boundary value", "R3",
            [f"{code}-CLAIM", "CLM-PAYOUT", "CLM-DEP-PARTS"],
            {**BASE_CAR, "car_age": 3.5, "addon": code, "claim_number": n, "cause": "accident", "parts": DAMAGE})
    add(f"Claim with {code} on a car exactly {max_age:g} years old", "claim", "boundary value", "R1",
        [f"{code}-ELIG", f"{code}-CLAIM"],
        {**BASE_CAR, "car_age": max_age, "addon": code, "claim_number": 1, "cause": "accident", "parts": DAMAGE})

    # R4 exclusions
    add(f"Theft claim with {code}: depreciation still deducted", "claim", "exclusion", "R4",
        [f"{code}-CLAIM"], {**BASE_CAR, "car_age": 3.5, "addon": code, "claim_number": 1, "cause": "theft", "parts": DAMAGE})
    add(f"Total loss with {code}: pays IDV minus deductible", "claim", "exclusion", "R4",
        [f"{code}-CLAIM", "CLM-TOTAL-LOSS"],
        {**BASE_CAR, "car_age": 3.5, "addon": code, "claim_number": 1, "cause": "total_loss", "parts": DAMAGE})

    # R6 renewal: before, exactly at, and past the limit after ageing one year
    for start, label in [(max_age - 1.5, "stays under"), (max_age - 1.0, "reaches exactly"), (max_age - 0.5, "goes past")]:
        add(f"Renewal: car {label} the limit ({start:g} -> {start + 1:g} yrs)", "renewal", "boundary value", "R6",
            [f"{code}-RENEW", "REN-AGE"], {**BASE_CAR, "car_age": start, "addon": code, "had_claim": False})
    return tests


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
