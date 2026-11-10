"""Extractors: turn document text into candidate Facts.

  baseline  hand-written patterns for the standard tariff tables (fast, precise, narrow)
  nova      Amazon Nova reading routed segments with a fixed schema (broad: also reads
            clause wording such as conditions and exclusions)

Both are untrusted until the grounding verifier has checked them.
"""
from __future__ import annotations

import json
import re

from ..llm import LLM
from .facts import AGE_BANDS, FACT_TYPES, Fact, age_band
from .ingest import Segment

PCT = r"(nil|\d+(?:\.\d+)?\s*%)"


def _pct(s: str) -> float:
    s = s.strip().lower()
    return 0.0 if s == "nil" else float(s.rstrip("%").strip())


def baseline(doc: str, pages: list[str]) -> list[Fact]:
    facts: list[Fact] = []
    table = None
    for pno, text in enumerate(pages, start=1):
        lines = [l.strip() for l in text.splitlines()]
        for i, line in enumerate(lines):
            low = line.lower()
            if "fixing idv" in low:
                table = "idv_depreciation"
            elif "wooden parts" in low or ("all other parts" in low and "depreciation" in low):
                table = "metal_depreciation"
            elif low.startswith(("idv of vehicles", "idv shall", "5) rate of depreciation for painting", "5. rate of depreciation")):
                table = None
            m = re.search(r"((?:not\s+)?exceeding\b.*?(?:months?|years?))\s+" + PCT + r"\s*$", line, re.I)
            if m and table:
                band = age_band(m.group(1))
                if band in AGE_BANDS:
                    facts.append(Fact(doc=doc, type=table, key=band, value=_pct(m.group(2)), quote=line, page=pno))
                continue
            m = re.search(PCT + r"\s*$", line, re.I) or re.search(r"-\s*" + PCT, line, re.I)
            if m and "depreciation" not in low[:20]:
                v = _pct(m.group(1))
                if "rubber" in low and "plastic" in low:
                    facts.append(Fact(doc=doc, type="parts_depreciation", key="rubber_nylon_plastic", value=v, quote=line, page=pno))
                    if "air bag" in low or "airbag" in low:
                        facts.append(Fact(doc=doc, type="parts_depreciation", key="airbags", value=v, quote=line, page=pno))
                elif "fibre glass" in low or "fiberglass" in low or "fibreglass" in low:
                    facts.append(Fact(doc=doc, type="parts_depreciation", key="fibreglass", value=v, quote=line, page=pno))
                elif "parts made of glass" in low:
                    facts.append(Fact(doc=doc, type="parts_depreciation", key="glass", value=v, quote=line, page=pno))
            if "preceding" in low:
                nxt = []
                for extra in lines[i + 1:i + 4]:
                    if "preceding" in extra.lower():
                        break
                    nxt.append(extra)
                    if re.search(r"\d+\s*%", extra):
                        break
                joined = " ".join([line] + nxt)
                pm = re.search(r"(\d+)\s*%", joined)
                words = {"two": "2", "three": "3", "four": "4", "five": "5"}
                ym = re.search(r"preceding\s+(\d|two|three|four|five)\s+consecutive", joined, re.I)
                key = (words.get(ym.group(1).lower(), ym.group(1)) if ym else
                       ("1" if re.search(r"preceding (full\s+)?year", joined, re.I) else None))
                if pm and key:
                    facts.append(Fact(doc=doc, type="ncb", key=key, value=float(pm.group(1)), quote=joined.strip(), page=pno))
            tm = re.search(r"exceeds\s+(\d+)\s*%\s+of\s+the\s+idv", line, re.I)
            if tm:
                facts.append(Fact(doc=doc, type="total_loss_threshold", key="pct_of_idv", value=float(tm.group(1)), quote=line, page=pno))
    return facts


SCHEMA = {
    "type": "object",
    "properties": {"facts": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": list(FACT_TYPES)},
            "key": {"type": "string"},
            "value": {"type": "string", "description": "number without % sign, 'true', 'unlimited' or 'per_schedule'"},
            "quote": {"type": "string", "description": "exact text copied from the passage that states the fact"}},
        "required": ["type", "key", "value", "quote"]}}},
    "required": ["facts"],
}


def _vocabulary() -> str:
    return "\n".join(f"- {t}: {spec['meaning']}. keys: {', '.join(spec['keys'])}. value: {spec['value']}"
                     for t, spec in FACT_TYPES.items())


SYSTEM = ("You extract facts from Indian motor insurance documents. Record only facts the passage states; "
          "never use outside knowledge. Copy the quote exactly from the passage (a single line or sentence). "
          "Use only these fact types and keys:\n" + _vocabulary() +
          "\nAge bands are in years: 'Not exceeding 6 months' is 0-0.5, 'Exceeding 1 year but not exceeding 2 "
          "years' is 1-2, 'Exceeding 10 years' is 10+. 'Nil' means 0. If nothing relevant is stated, return an empty list.")


def _context(passage: str, quote: str, heading: str, title: str, chars: int = 1500) -> str:
    """The text a fact sits under: document title, nearest heading, and up to `chars`
    characters of the passage above the quote. Used to check which cover a clause belongs to."""
    sq = lambda t: re.sub(r"\s+", "", t).lower()
    p, q = sq(passage), sq(quote)
    i = p.find(q[:40]) if q else -1
    above = p[max(0, i - chars):i] if i >= 0 else ""
    return " | ".join(x for x in (title, heading, above) if x)


def nova(llm: LLM, segs: list[Segment], feedback: dict[int, str] | None = None,
         only: set[int] | None = None, title: str = "") -> list[Fact]:
    facts: list[Fact] = []
    for idx, s in enumerate(segs):
        if only is not None and idx not in only:
            continue
        text = f"Document: {s.doc}, page {s.page}\nPassage:\n{s.text}"
        if feedback and idx in feedback:
            text += "\n\nYour previous answer for this passage was rejected: " + feedback[idx]
        try:
            out = llm.call_tool("extract", SYSTEM, text, "record_facts", SCHEMA, max_tokens=1800)
        except Exception as exc:  # one bad segment must not stop the run
            continue
        m = re.match(r"\[Section: (.*)\]", s.text)
        heading = m.group(1) if m else ""
        for f in out.get("facts", []):
            try:
                facts.append(Fact(doc=s.doc, page=s.page, source="nova", seg=idx, section=_context(s.text, f.get("quote", ""), heading, title), type=f["type"], key=str(f["key"]).strip(),
                                  value=f["value"], quote=f.get("quote", "")))
            except Exception:
                continue
    return facts
