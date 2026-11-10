"""Loads the rule catalog and applies a ChangeRequest to it (deterministically)."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from .models import ChangeRequest, Rule

DATA = Path(__file__).resolve().parent.parent / "data"


class Catalog:
    def __init__(self, rules: list[Rule], product: str = ""):
        self.product = product
        self.rules = {r.id: r for r in rules}

    @classmethod
    def load(cls, path: Path | None = None) -> "Catalog":
        raw = json.loads((path or DATA / "rules.json").read_text())
        return cls([Rule(**r) for r in raw["rules"]], raw.get("product", ""))

    def get(self, rule_id: str) -> Rule:
        return self.rules[rule_id]

    def param(self, rule_id: str, key: str):
        return self.rules[rule_id].params[key]

    def has(self, rule_id: str) -> bool:
        return rule_id in self.rules

    def copy(self) -> "Catalog":
        return Catalog([r.model_copy(deep=True) for r in self.rules.values()], self.product)


def apply_change(base: Catalog, change: ChangeRequest) -> tuple[Catalog, list[str], list[str]]:
    """Returns (new catalog, new rule ids, changed rule ids)."""
    cat = base.copy()
    new_ids: list[str] = []
    changed_ids: list[str] = []

    if change.kind == "new_addon":
        code = (change.addon_code or "ADDON").upper()
        name = change.addon_name or code
        max_age = change.max_car_age_years
        limit = change.claim_limit_per_year
        new_rules = [
            Rule(id=f"{code}-ELIG", area="underwriting", name=f"{name}: eligibility",
                 description=f"Available only for cars up to {max_age} years old at policy start (inclusive).",
                 inputs=["car_age"], outputs=[f"{code.lower()}_eligible"],
                 params={"max_age": max_age}, is_new=True),
            Rule(id=f"{code}-PRICE", area="rating", name=f"{name}: premium",
                 description=f"Add-on premium = {change.price_pct_of_od:.0%} of the OD premium (after NCB).",
                 inputs=["od_premium", f"{code.lower()}_eligible"], outputs=["addon_premiums", "addon_list"],
                 params={"pct_of_od": change.price_pct_of_od}, is_new=True),
            Rule(id=f"{code}-CLAIM", area="claims", name=f"{name}: claim benefit",
                 description=(f"No depreciation deducted on replaced parts for the first {limit} claims a year; "
                              f"not for {', '.join(change.excluded_causes)}."),
                 inputs=[f"{code.lower()}_eligible", "claim_number", "claim_cause"], outputs=["depreciation_amount"],
                 params={"claim_limit": limit, "excluded_causes": change.excluded_causes}, is_new=True),
            Rule(id=f"{code}-RENEW", area="renewal", name=f"{name}: renewal eligibility",
                 description=f"At renewal, drop the add-on once the car is older than {max_age} years.",
                 inputs=["renewal_car_age"], outputs=[f"{code.lower()}_eligible"],
                 params={"max_age": max_age}, is_new=True),
        ]
        if change.allow_midterm:
            new_rules.append(
                Rule(id=f"{code}-MIDTERM", area="billing", name=f"{name}: mid-term addition",
                     description="Can be added during the year; charged only for the remaining months.",
                     inputs=[f"{code.lower()}_eligible", "month_added"], outputs=["endorsement_premium"],
                     params={"proration": "remaining_months"}, attributes={"midterm": True}, is_new=True))
        for r in new_rules:
            cat.rules[r.id] = r
            new_ids.append(r.id)

    elif change.kind == "modify_parameter":
        if not cat.has(change.rule_id or ""):
            raise ValueError(f"unknown rule_id {change.rule_id!r}")
        rule = cat.get(change.rule_id)
        target = rule.params
        try:
            for key in change.param_path[:-1]:
                target = target[key]
            target[change.param_path[-1]]
        except (KeyError, TypeError, IndexError):
            raise ValueError(f"param_path {change.param_path} not found in {rule.id}; params are {rule.params}")
        target[change.param_path[-1]] = change.new_value
        changed_ids.append(rule.id)

    return cat, new_ids, changed_ids


def addon_code(change: ChangeRequest) -> str:
    return (change.addon_code or "ADDON").upper()
