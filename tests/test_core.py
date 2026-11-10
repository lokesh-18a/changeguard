"""Unit tests for the deterministic core. Run: pytest -q"""
import pytest

from changeguard import oracle
from changeguard.catalog import Catalog, apply_change
from changeguard.impact import find_conflicts, find_impact
from changeguard.models import ChangeRequest
from changeguard.pipeline import run

ZD = ChangeRequest(kind="new_addon", summary="Zero Dep", addon_code="ZD", addon_name="Zero Depreciation",
                   max_car_age_years=5, price_pct_of_od=0.15, claim_limit_per_year=2, allow_midterm=True)
CAR = {"ex_showroom_price": 800_000, "engine_cc": 1200, "claim_free_years": 1}
DAMAGE = [{"type": "plastic", "cost": 10_000}, {"type": "metal", "cost": 20_000}]


@pytest.fixture
def cat():
    return apply_change(Catalog.load(), ZD)[0]


def test_worked_example_matches_doc():
    """3.5-year-old car, plastic bumper 10,000 + metal door 20,000: insurer pays 19,000."""
    out = oracle.claim(Catalog.load(), {**CAR, "car_age": 3.5, "cause": "accident", "parts": DAMAGE})
    assert out["depreciation"] == 10_000 and out["payout"] == 19_000


def test_zero_dep_waives_depreciation(cat):
    out = oracle.claim(cat, {**CAR, "car_age": 3.5, "addon": "ZD", "claim_number": 1, "cause": "accident", "parts": DAMAGE})
    assert out["payout"] == 29_000


def test_claim_limit(cat):
    out = oracle.claim(cat, {**CAR, "car_age": 3.5, "addon": "ZD", "claim_number": 3, "cause": "accident", "parts": DAMAGE})
    assert out["depreciation"] == 10_000


def test_age_limit_is_inclusive(cat):
    assert oracle.eligible_for_addon(cat, "ZD", 5.0)
    assert not oracle.eligible_for_addon(cat, "ZD", 5.1)


def test_impact_reaches_billing_and_documents(cat):
    base = Catalog.load()
    new_cat, new_ids, changed = apply_change(base, ZD)
    ids = {i.rule_id for i in find_impact(new_cat, new_ids, changed)}
    assert {"RAT-TOTAL", "BIL-ENDORSE", "CLM-PAYOUT", "DOC-WORDING"} <= ids


def test_generic_conflicts_find_hidden_refund_gap():
    new_cat, new_ids, _ = apply_change(Catalog.load(), ZD)
    msgs = " ".join(c.message for c in find_conflicts(new_cat, new_ids))
    assert "BIL-CANCEL-REFUND" in msgs and "BIL-ANNUAL" in msgs and "REN-AGE" in msgs


def test_correct_system_passes_everything():
    rep = run("Introduce a Zero Depreciation add-on for cars up to 5 years old, 15% of OD premium, "
              "2 claims per year, can be added mid-term.", offline=True, mutants=0)
    assert rep.passed == len(rep.results) and len(rep.results) > 30


def test_mutation_audit_beats_typical_suite():
    rep = run("Introduce a Zero Depreciation add-on for cars up to 5 years old, 15% of OD premium, "
              "2 claims per year, can be added mid-term.", offline=True)
    m = rep.mutation
    assert m["mutants"] == m["total_points"] > 50
    assert m["score"] > m["baseline_score"] and m["score"] >= 0.8


def test_random_sample_is_seeded():
    from changeguard import mutation
    a = [m.id for m in mutation.sample(10, seed=3)]
    b = [m.id for m in mutation.sample(10, seed=3)]
    c = [m.id for m in mutation.sample(10, seed=4)]
    assert a == b and a != c


def test_boundary_tests_derived_from_rule_data():
    rep = run("Zero Dep for cars up to 7 years, 20% of OD, 1 claim per year", offline=True, mutants=0)
    ages = sorted({t.inputs["car_age"] for t in rep.tests if t.rules == ["ZD-ELIG"] and t.kind == "quote"})
    assert ages == [6.9, 7.0, 7.1]


def test_parameter_change_marks_stale_tests():
    rep = run("Raise the compulsory deductible for cars from 1,000 to 1,500 cc to Rs 1,500", offline=True)
    assert {t.id for t in rep.tests if t.stale} == {"RT-04", "RT-06"}
