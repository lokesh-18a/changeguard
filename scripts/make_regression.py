"""Builds data/regression_tests.json: the insurer's EXISTING regression suite,
with expected values computed on the product BEFORE any change."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from changeguard import oracle
from changeguard.catalog import DATA, Catalog
from changeguard.testgen import COMPARE

cat = Catalog.load()
car = {"ex_showroom_price": 800_000, "engine_cc": 1200, "claim_free_years": 1}
damage = [{"name": "front bumper", "type": "plastic", "cost": 10_000},
          {"name": "left door", "type": "metal", "cost": 20_000}]
specs = [
 ("RT-01", "Quote: 3-year-old car with roadside assistance", "quote", ["RAT-IDV", "RAT-OD-PREM", "RAT-TOTAL", "RAT-GST"], {**car, "car_age": 3.0, "addons": ["RSA"]}),
 ("RT-02", "Quote: 12-year-old car, 1,600 cc engine", "quote", ["RAT-IDV", "RAT-OD-RATE", "RAT-TP-PREM"], {**car, "car_age": 12.0, "engine_cc": 1600}),
 ("RT-03", "Quote: brand-new car, no NCB", "quote", ["RAT-IDV", "RAT-NCB"], {**car, "car_age": 0.3, "claim_free_years": 0}),
 ("RT-04", "Claim: bumper and door on a 3.5-year-old car", "claim", ["CLM-DEP-PARTS", "CLM-DEDUCT", "CLM-PAYOUT"], {**car, "car_age": 3.5, "cause": "accident", "parts": damage}),
 ("RT-05", "Claim: 1,600 cc car pays the higher deductible", "claim", ["CLM-DEDUCT", "CLM-PAYOUT"], {**car, "car_age": 2.0, "engine_cc": 1600, "cause": "accident", "parts": damage}),
 ("RT-06", "Claim: repair above 75% of IDV is a total loss", "claim", ["CLM-TOTAL-LOSS", "CLM-PAYOUT"], {**car, "car_age": 6.0, "cause": "accident", "parts": [{"name": "body shell", "type": "metal", "cost": 300_000}]}),
 ("RT-07", "Renewal: claim-free year raises NCB", "renewal", ["REN-AGE", "RAT-NCB"], {**car, "car_age": 4.5, "claim_free_years": 2, "had_claim": False}),
 ("RT-08", "Renewal: a claim resets NCB to zero", "renewal", ["REN-AGE", "CLM-NCB-RESET"], {**car, "car_age": 4.5, "claim_free_years": 2, "had_claim": True}),
]
tests = []
for tid, title, kind, rules, inp in specs:
    res = oracle.expected(cat, kind, inp)
    tests.append({"id": tid, "title": title, "kind": kind, "technique": "existing regression test",
                  "requirement": "existing", "rules": rules, "inputs": inp,
                  "expected": {k: res[k] for k in COMPARE[kind] if k in res}})
(DATA / "regression_tests.json").write_text(json.dumps({"note": "Existing regression suite, expected values from product v1.0", "tests": tests}, indent=1))
print(f"wrote {len(tests)} tests")
