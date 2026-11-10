"""Loads the rule catalog and applies a ChangeRequest to it (deterministically)."""
from __future__ import annotations

import json
from pathlib import Path

from .models import ChangeRequest, Rule

DATA = Path(__file__).resolve().parent.parent / "data"


class Catalog:
    def __init__(self, rules: list[Rule], product: str = "", families: dict | None = None,
                 varies_at_renewal: list[str] | None = None):
        self.product = product
        self.rules = {r.id: r for r in rules}
        self.families = families or {}
        self.varies_at_renewal = varies_at_renewal or []

    @classmethod
    def load(cls, path: Path | None = None) -> "Catalog":
        raw = json.loads((path or DATA / "rules.json").read_text())
        return cls([Rule(**r) for r in raw["rules"]], raw.get("product", ""),
                   raw.get("field_families", {}), raw.get("varies_at_renewal", []))

    def get(self, rule_id: str) -> Rule:
        return self.rules[rule_id]

    def param(self, rule_id: str, key: str):
        return self.rules[rule_id].params[key]

    def has(self, rule_id: str) -> bool:
        return rule_id in self.rules

    def copy(self) -> "Catalog":
        return Catalog([r.model_copy(deep=True) for r in self.rules.values()], self.product,
                       json.loads(json.dumps(self.families)), list(self.varies_at_renewal))


def addon_code(change: ChangeRequest) -> str:
    return (change.addon_code or "ADDON").upper()


def addon_rules(change: ChangeRequest) -> list[Rule]:
    """Compiles a new add-on into rules. Each rule declares the data it reads and writes
    (so the graph links itself), how tests can exercise its limits, and the capabilities
    it introduces (tags), which conflict checks compare against existing assumptions."""
    code = addon_code(change)
    low = code.lower()
    name = change.addon_name or code
    max_age, limit = change.max_car_age_years, change.claim_limit_per_year
    rules = [
        Rule(id=f"{code}-ELIG", area="underwriting", name=f"{name}: eligibility",
             description=f"Available only for cars up to {max_age:g} years old at policy start.",
             inputs=["car_age"], outputs=[f"{low}_eligible"], params={"max_age": max_age},
             exercise=[{"param": "max_age", "input": "car_age", "step": 0.1,
                        "ops": ["quote", "claim"] + (["midterm"] if change.allow_midterm else [])}],
             tags=["age_limited_cover"], is_new=True),
        Rule(id=f"{code}-PRICE", area="rating", name=f"{name}: premium",
             description=f"Priced at {change.price_pct_of_od:.0%} of the own-damage premium (after NCB).",
             inputs=["od_premium", f"{low}_eligible"], outputs=["addon_premiums", "addon_list"],
             params={"pct_of_od": change.price_pct_of_od},
             exercise=[{"param": None, "input": None, "ops": ["quote"], "typical": True},
                       {"param": "pct_of_od", "input": "claim_free_years", "ops": ["quote"], "values": [0, 5]}],
             tags=["new_cover"], is_new=True),
        Rule(id=f"{code}-CLAIM", area="claims", name=f"{name}: claim benefit",
             description=(f"No depreciation deducted for up to {limit} claims per policy year; "
                          f"not for {', '.join(change.excluded_causes)}."),
             inputs=[f"{low}_eligible", "claim_number", "claim_cause"], outputs=["depreciation_amount"],
             params={"claim_limit": limit, "excluded_causes": change.excluded_causes},
             exercise=[{"param": "claim_limit", "input": "claim_number", "ops": ["claim"], "step": 1},
                       {"param": "excluded_causes", "input": "cause", "ops": ["claim"], "categorical": True}],
             is_new=True),
        Rule(id=f"{code}-RENEW", area="renewal", name=f"{name}: renewal eligibility",
             description=f"Dropped at renewal once the car is older than {max_age:g} years.",
             inputs=["renewal_car_age"], outputs=[f"{low}_eligible"], params={"max_age": max_age},
             exercise=[{"param": "max_age", "input": "car_age", "ops": ["renewal"], "step": 0.1, "offset": -1}],
             is_new=True),
    ]
    if change.allow_midterm:
        rules.append(Rule(
            id=f"{code}-MIDTERM", area="billing", name=f"{name}: mid-term addition",
            description="Can be added during the policy year, charged only for the remaining months.",
            inputs=[f"{low}_eligible", "month_added"], outputs=["endorsement_premium"],
            params={"months": [1, 12]},
            exercise=[{"param": "months", "input": "month_added", "ops": ["midterm"], "range": True}],
            tags=["midterm_premium_change", "addon_midterm"], is_new=True))
    return rules


def apply_change(base: Catalog, change: ChangeRequest) -> tuple[Catalog, list[str], list[str]]:
    """Returns (new catalog, new rule ids, changed rule ids)."""
    cat = base.copy()
    new_ids: list[str] = []
    changed_ids: list[str] = []

    if change.kind == "new_addon":
        for r in addon_rules(change):
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
