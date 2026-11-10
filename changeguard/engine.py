"""The 'system under test': a stand-in for a carrier's configured policy, billing and claims system.

It is written separately from the oracle (oracle.py), the way a carrier's Guidewire or
Duck Creek configuration is separate from the product specification. It contains no
deliberate bugs. To measure how good the tests are, mutation.py injects RANDOM small
defects into this file (for example `<=` becomes `<`, `+` becomes `-`) and checks how
many of them the generated tests catch.
"""
from __future__ import annotations

from .catalog import Catalog


def _lookup(table, value):
    for upper, out in table:
        if value <= upper:
            return out
    return table[-1][1]


def _band(cat, cc):
    return _lookup(cat.param("VEH-CC", "bands"), cc)


def _addon_ok(cat: Catalog, code: str, age: float) -> bool:
    return age <= cat.param(f"{code}-ELIG", "max_age")


def _gst(cat: Catalog, amount: float) -> int:
    return round(amount * (1 + cat.param("RAT-GST", "rate")))


def quote(cat: Catalog, req: dict) -> dict:
    age = req["car_age"]
    idv = round(req["ex_showroom_price"] * (1 - _lookup(cat.param("RAT-IDV", "depreciation_by_age"), age)))
    ncb_slab = cat.param("RAT-NCB", "slab")
    ncb = ncb_slab[min(req.get("claim_free_years", 0), len(ncb_slab) - 1)]
    od = round(idv * _lookup(cat.param("RAT-OD-RATE", "rate_by_age"), age) * (1 - ncb))
    tp = cat.param("RAT-TP-PREM", "premium_by_band")[_band(cat, req["engine_cc"])]
    addons, rejected = {}, []
    for code in req.get("addons", []):
        if code == "RSA":
            addons[code] = cat.param("RAT-ADDON-RSA", "flat_premium")
        elif cat.has(f"{code}-PRICE"):
            if _addon_ok(cat, code, age):
                addons[code] = round(od * cat.param(f"{code}-PRICE", "pct_of_od"))
            else:
                rejected.append(code)
    net = od + tp + sum(addons.values())
    return {"idv": idv, "od_premium": od, "tp_premium": tp, "addon_premiums": addons,
            "rejected_addons": rejected, "net_premium": net, "total_premium": _gst(cat, net)}


def midterm(cat: Catalog, req: dict) -> dict:
    code = req["addon"]
    if not _addon_ok(cat, code, req["car_age"]):
        return {"accepted": False, "charge": 0}
    annual = quote(cat, {**req, "addons": [code]})["addon_premiums"][code]
    remaining_months = 13 - req["month_added"]
    return {"accepted": True, "charge": _gst(cat, annual * remaining_months / 12)}


def claim(cat: Catalog, req: dict) -> dict:
    age = req["car_age"]
    idv = quote(cat, {**req, "addons": []})["idv"]
    deductible = cat.param("CLM-DEDUCT", "by_band")[_band(cat, req["engine_cc"])]
    cost = sum(p["cost"] for p in req["parts"])
    if req.get("cause") == "total_loss" or cost > cat.param("CLM-TOTAL-LOSS", "threshold") * idv:
        return {"total_loss": True, "depreciation": 0, "deductible": deductible, "payout": max(idv - deductible, 0)}
    code = req.get("addon")
    waive = False
    if code and cat.has(f"{code}-CLAIM") and _addon_ok(cat, code, age):
        rule = cat.get(f"{code}-CLAIM").params
        waive = req.get("claim_number", 1) <= rule["claim_limit"] and req.get("cause") not in rule["excluded_causes"]
    dep = 0
    if not waive:
        for p in req["parts"]:
            rate = cat.param("CLM-DEP-PARTS", "fixed_by_type").get(p["type"])
            if rate is None:
                rate = _lookup(cat.param("CLM-DEP-PARTS", "metal_by_age"), age)
            dep += round(p["cost"] * rate)
    return {"total_loss": False, "depreciation": dep, "deductible": deductible, "payout": max(cost - dep - deductible, 0)}


def renewal(cat: Catalog, req: dict) -> dict:
    new_age = round(req["car_age"] + 1, 2)
    code = req.get("addon")
    keep = bool(code) and cat.has(f"{code}-RENEW") and new_age <= cat.param(f"{code}-RENEW", "max_age")
    years = 0 if req.get("had_claim") else req.get("claim_free_years", 0) + 1
    slab = cat.param("RAT-NCB", "slab")
    return {"renewal_car_age": new_age, "addon_retained": keep, "ncb_pct": slab[min(years, len(slab) - 1)]}


OPERATIONS = {"quote": quote, "midterm": midterm, "claim": claim, "renewal": renewal}


def run(cat: Catalog, kind: str, inputs: dict, ops: dict | None = None) -> dict:
    return (ops or OPERATIONS)[kind](cat, inputs)
