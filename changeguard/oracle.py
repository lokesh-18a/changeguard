"""Test oracle: the reference calculation of what the product rules SAY should happen.

It reads every number from the rule catalog, so when a change updates the
catalog, the expected results update with it. It is plain, deterministic
Python: no language model is involved in deciding a correct answer.
"""
from __future__ import annotations

from .catalog import Catalog


def band(table, x):
    """First value whose upper bound (inclusive) is >= x."""
    for upper, value in table:
        if x <= upper:
            return value
    return table[-1][1]


def cc_band(cat: Catalog, cc: int) -> str:
    return band(cat.param("VEH-CC", "bands"), cc)


def _addon_codes(cat: Catalog) -> list[str]:
    return sorted({rid.split("-")[0] for rid in cat.rules if rid.endswith("-PRICE")})


def eligible_for_addon(cat: Catalog, code: str, car_age: float) -> bool:
    return car_age <= cat.param(f"{code}-ELIG", "max_age")


def quote(cat: Catalog, inp: dict) -> dict:
    age = inp["car_age"]
    idv = round(inp["ex_showroom_price"] * (1 - band(cat.param("RAT-IDV", "depreciation_by_age"), age)))
    rate = band(cat.param("RAT-OD-RATE", "rate_by_age"), age)
    slab = cat.param("RAT-NCB", "slab")
    ncb = slab[min(inp.get("claim_free_years", 0), len(slab) - 1)]
    od = round(idv * rate * (1 - ncb))
    tp = cat.param("RAT-TP-PREM", "premium_by_band")[cc_band(cat, inp["engine_cc"])]

    addons, rejected = {}, []
    for code in inp.get("addons", []):
        if code == "RSA":
            addons["RSA"] = cat.param("RAT-ADDON-RSA", "flat_premium")
        elif code in _addon_codes(cat):
            if eligible_for_addon(cat, code, age):
                addons[code] = round(od * cat.param(f"{code}-PRICE", "pct_of_od"))
            else:
                rejected.append(code)
    net = od + tp + sum(addons.values())
    total = round(net * (1 + cat.param("RAT-GST", "rate")))
    return {"idv": idv, "od_premium": od, "tp_premium": tp, "addon_premiums": addons,
            "rejected_addons": rejected, "net_premium": net, "total_premium": total}


def midterm_charge(cat: Catalog, inp: dict) -> dict:
    """Charge for adding an add-on in month `month_added` (1 = first month)."""
    code = inp["addon"]
    if not eligible_for_addon(cat, code, inp["car_age"]):
        return {"accepted": False, "charge": 0}
    q = quote(cat, {**inp, "addons": [code]})
    annual = q["addon_premiums"][code]
    remaining = 13 - inp["month_added"]
    charge = round(annual * remaining / 12 * (1 + cat.param("RAT-GST", "rate")))
    return {"accepted": True, "charge": charge}


def claim(cat: Catalog, inp: dict) -> dict:
    age = inp["car_age"]
    q = quote(cat, {**inp, "addons": []})
    idv = q["idv"]
    deductible = cat.param("CLM-DEDUCT", "by_band")[cc_band(cat, inp["engine_cc"])]
    parts = inp["parts"]
    repair_cost = sum(p["cost"] for p in parts)

    if repair_cost > cat.param("CLM-TOTAL-LOSS", "threshold") * idv or inp.get("cause") == "total_loss":
        return {"total_loss": True, "depreciation": 0, "deductible": deductible, "payout": max(idv - deductible, 0)}

    zd_applies = False
    code = inp.get("addon")
    if code and cat.has(f"{code}-CLAIM") and eligible_for_addon(cat, code, age):
        p = cat.get(f"{code}-CLAIM").params
        zd_applies = inp.get("claim_number", 1) <= p["claim_limit"] and inp.get("cause") not in p["excluded_causes"]

    dep = 0
    if not zd_applies:
        fixed = cat.param("CLM-DEP-PARTS", "fixed_by_type")
        for part in parts:
            pct = fixed.get(part["type"], band(cat.param("CLM-DEP-PARTS", "metal_by_age"), age))
            dep += round(part["cost"] * pct)
    payout = max(repair_cost - dep - deductible, 0)
    return {"total_loss": False, "depreciation": dep, "deductible": deductible, "payout": payout}


def renewal(cat: Catalog, inp: dict) -> dict:
    new_age = round(inp["car_age"] + 1, 2)
    code = inp.get("addon")
    retained = bool(code) and cat.has(f"{code}-RENEW") and new_age <= cat.param(f"{code}-RENEW", "max_age")
    years = 0 if inp.get("had_claim") else inp.get("claim_free_years", 0) + 1
    slab = cat.param("RAT-NCB", "slab")
    return {"renewal_car_age": new_age, "addon_retained": retained, "ncb_pct": slab[min(years, len(slab) - 1)]}


CALCULATORS = {"quote": quote, "midterm": midterm_charge, "claim": claim, "renewal": renewal}


def expected(cat: Catalog, kind: str, inputs: dict) -> dict:
    return CALCULATORS[kind](cat, inputs)
