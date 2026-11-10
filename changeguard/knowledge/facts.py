"""Fact model and the canonical vocabulary every extractor must use.

A Fact is one checkable statement from a source document, e.g.
  (raheja_pc, metal_depreciation, "1-2", 10)   "Exceeding 1 year but not exceeding 2 years 10%"
Canonical keys make facts from different insurers comparable.
"""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

AGE_BANDS = ["0-0.5", "0.5-1", "1-2", "2-3", "3-4", "4-5", "5-10", "10+"]

FACT_TYPES: dict[str, dict] = {
    "parts_depreciation": {"keys": ["rubber_nylon_plastic", "fibreglass", "glass", "airbags", "paint_material"],
                           "value": "percent", "meaning": "depreciation % deducted on replaced parts by material"},
    "metal_depreciation": {"keys": AGE_BANDS, "value": "percent",
                           "meaning": "depreciation % on metal and other parts by vehicle age band (years)"},
    "idv_depreciation": {"keys": AGE_BANDS[:6], "value": "percent",
                         "meaning": "depreciation % used to fix IDV by vehicle age band (years)"},
    "ncb": {"keys": ["1", "2", "3", "4", "5"], "value": "percent",
            "meaning": "No Claim Bonus % by number of consecutive claim-free years"},
    "total_loss_threshold": {"keys": ["pct_of_idv"], "value": "percent",
                             "meaning": "repair cost above this % of IDV makes it a constructive total loss"},
    "zero_dep_claim_limit": {"keys": ["max_claims"], "value": "integer, 'unlimited' or 'per_schedule'",
                             "meaning": "how many claims per policy period the zero-depreciation cover pays"},
    "zero_dep_condition": {"keys": ["authorised_garage_required", "deductible_applies", "excludes_total_loss",
                                    "excludes_theft", "expires_on_transfer", "excludes_tyres_battery"],
                           "value": "true", "meaning": "conditions and exclusions of the zero-depreciation cover"},
    "zero_dep_non_authorised_payout": {"keys": ["pct_of_claim"], "value": "percent",
                                       "meaning": "share paid if repaired at a non-authorised garage"},
}


class Fact(BaseModel):
    doc: str
    type: str
    key: str
    value: Any
    quote: str = Field("", description="verbatim text from the document that states this fact")
    page: int | None = None
    source: str = "baseline"   # which extractor produced it
    seg: int | None = None     # which segment it came from (for feedback on retry)
    section: str = ""          # heading of the passage it came from (or the document title)

    def identity(self) -> tuple:
        return (self.doc, self.type, self.key, norm_value(self.value))


def norm_value(v: Any):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().lower().rstrip("%")
    if s in ("nil", "none", "zero"):
        return 0.0
    if s in ("true", "yes"):
        return True
    try:
        return float(s)
    except ValueError:
        return s.replace(" ", "_")


_NUM = r"(\d+(?:\.\d+)?)\s*(months?|years?|yrs?)"


def age_band(text: str) -> str | None:
    """'Exceeding 1 year but not exceeding 2 years' -> '1-2'; 'Not exceeding 6 months' -> '0-0.5'."""
    t = text.lower().replace("6months", "6 months")
    nums = [float(n) / (12 if u.startswith("month") else 1) for n, u in re.findall(_NUM, t)]
    fmt = lambda x: f"{x:g}"
    if t.strip().startswith("not exceeding") and nums:
        return f"0-{fmt(nums[0])}"
    if "exceeding" in t and "not exceeding" in t and len(nums) >= 2:
        return f"{fmt(nums[0])}-{fmt(nums[1])}"
    if "exceeding" in t and len(nums) == 1:
        return f"{fmt(nums[0])}+"
    return None
