"""Grounding verifier (code, not a model). A candidate fact is accepted only if:

  1. its type and key are in the canonical vocabulary
  2. its value has the right kind (a percentage is a number; a condition is true;
     a claim limit is a number, 'unlimited' or 'per_schedule')
  3. its quote really appears in the source document (whitespace-insensitive)
  4. its value is actually written in that quote (a number, or 'Nil' for 0)
  5. the quote is ABOUT the fact: it names the material, states the same age band,
     mentions claims and the right number of years, or the condition's subject
  6. clause facts about the zero-depreciation cover come from a passage under a
     zero-depreciation heading, or name the cover themselves

Version 1 had checks 1, 3, 4 and part of 5. Nova's first measured run showed that was
not enough (tyre-clause percentages accepted as rubber-part depreciation, words such as
'notwithstanding' accepted as NCB values), so checks 2, 5 and 6 were added. They are
generic per fact type, not tuned to individual answers.

Rejected facts carry a reason, which is fed back to the extractor for one retry.
"""
from __future__ import annotations

import re

from .facts import FACT_TYPES, Fact, age_band, norm_value

CLAUSE_WORDS = {
    "authorised_garage_required": r"authori[sz]ed",
    "deductible_applies": r"deductible|excess",
    "excludes_total_loss": r"total loss",
    "excludes_theft": r"theft",
    "expires_on_transfer": r"transfer",
    "excludes_tyres_battery": r"tyre",
    "max_claims": r"claim",
    "pct_of_claim": r"\d+\s*%",
}
MATERIAL_WORDS = {
    "rubber_nylon_plastic": r"rubber|nylon|plastic",
    "fibreglass": r"fib(re|er)\s*glass",
    "glass": r"glass",
    "airbags": r"air\s*bag",
    "paint_material": r"paint",
}
ZERO_DEP = re.compile(r"zero ?depreciation|nil ?depreciation|depreciation ?(shield|waiver|cover|reimbursement)|bumper", re.I)
YEAR_WORDS = {"1": r"preceding (full )?year|\b1\b|\bone\b", "2": r"\b2\b|\btwo\b", "3": r"\b3\b|\bthree\b",
              "4": r"\b4\b|\bfour\b", "5": r"\b5\b|\bfive\b"}
AGE_PHRASE = re.compile(r"(not\s+)?exceeding\b.*?(?:months?|years?)(?:\s+but\s+not\s+exceeding\b.*?(?:months?|years?))?", re.I)


def _squash(s: str) -> str:
    return re.sub(r"[\s ]+", "", s).lower()


def canonical_value(f: Fact):
    """Maps harmless variants onto the canonical vocabulary ('as per schedule' -> 'per_schedule')."""
    v = norm_value(f.value)
    if f.key == "max_claims" and isinstance(v, str):
        if "schedule" in v:
            return "per_schedule"
        if v in ("unlimited", "every", "all", "no_limit"):
            return "unlimited"
    return v


def check(f: Fact, doc_text_squashed: str) -> str | None:
    spec = FACT_TYPES.get(f.type)
    if not spec:
        return f"unknown fact type {f.type!r}"
    if f.key not in spec["keys"]:
        return f"key {f.key!r} is not one of {spec['keys']} for {f.type}"
    v = canonical_value(f)
    # 2. kind of value
    if spec["value"] == "percent" and not isinstance(v, float):
        return f"value {f.value!r} is not a percentage"
    if f.type == "zero_dep_condition" and v is not True:
        return "a condition fact must be true (record only conditions the text states)"
    if f.key == "max_claims" and not (isinstance(v, float) or v in ("unlimited", "per_schedule")):
        return f"claim limit {f.value!r} is not a number, 'unlimited' or 'per_schedule'"
    # 3. grounding
    q = _squash(f.quote)
    if len(q) < 8:
        return "quote is missing or too short"
    if q not in doc_text_squashed:
        return "quote does not appear in the document"
    # 4. value written in the quote
    if isinstance(v, float):
        num = f"{v:g}"
        if not (re.search(rf"(?<![\d.]){re.escape(num)}(?![\d])", f.quote) or (v == 0 and re.search(r"\bnil\b", f.quote, re.I))):
            if not (f.key == "max_claims" and re.search(rf"\b({'one' if v == 1 else 'two' if v == 2 else 'xx'})\b", f.quote, re.I)):
                return f"value {num} is not written in the quote"
    # 5. the quote is about this fact
    if f.type == "parts_depreciation" and not re.search(MATERIAL_WORDS[f.key], f.quote, re.I):
        return f"quote does not mention the material {f.key}"
    if f.type in ("metal_depreciation", "idv_depreciation"):
        m = AGE_PHRASE.search(f.quote)
        if not m or age_band(m.group(0)) != f.key:
            return f"quote does not state the age band {f.key}"
    if f.type == "ncb":
        if not re.search(r"claim", f.quote, re.I) or not re.search(YEAR_WORDS[f.key], f.quote, re.I):
            return f"quote does not state {f.key} claim-free year(s)"
    if f.type == "total_loss_threshold" and not re.search(r"total loss|\bctl\b|idv", f.quote, re.I):
        return "quote is not about total loss"
    pat = CLAUSE_WORDS.get(f.key)
    if pat and not re.search(pat, f.quote, re.I):
        return f"quote does not mention what {f.key} is about"
    # 6. zero-depreciation facts must come from a zero-depreciation passage
    if f.type.startswith("zero_dep") and not (ZERO_DEP.search(f.section or "") or ZERO_DEP.search(f.quote)):
        return "passage is not about the zero-depreciation cover"
    return None


def verify(facts: list[Fact], doc_texts: dict[str, str]) -> tuple[list[Fact], list[tuple[Fact, str]]]:
    squashed = {d: _squash(t) for d, t in doc_texts.items()}
    ok, rejected, seen = [], [], set()
    for f in facts:
        reason = check(f, squashed.get(f.doc, ""))
        if reason:
            rejected.append((f, reason))
            continue
        f.value = canonical_value(f) if not isinstance(canonical_value(f), float) else f.value
        if f.identity() not in seen:
            seen.add(f.identity())
            ok.append(f)
    return ok, rejected
