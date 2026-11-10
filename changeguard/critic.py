"""Writer + Critic: an evaluator-optimizer loop where the evaluator is code, not another model.

The Writer (language model) drafts the report from computed facts. The Critic checks it:

  1. every rule, test and conflict ID it mentions exists
  2. every number it states appears in the computed facts (no invented figures)
  3. the recommendation agrees with the evidence (no "ready to release" when tests fail
     or high-severity conflicts are open)

If the draft fails, the Writer gets the critic's findings and one rewrite. If it still
fails, a deterministic template report is used instead, so a wrong report never ships.
"""
from __future__ import annotations

import json
import re

from .catalog import Catalog
from .llm import LLM, _offline_explain
from .models import TestCase

ID_RE = re.compile(r"\b(?:[A-Z]{2,6}(?:-[A-Z0-9]+)+|GT-\d+|RT-\d+|C\d+)\b")
NUM_RE = re.compile(r"(?<![A-Za-z\-])(\d+(?:[.,]\d+)*)(%?)")
POSITIVE = re.compile(r"\b(ready (for|to) (release|sign-?off)|safe to release|no issues|approve(d)? for release)\b", re.I)


def _numbers_in(obj) -> set[float]:
    out: set[float] = set()
    for m in NUM_RE.finditer(json.dumps(obj)):
        try:
            v = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        out |= {v, round(v * 100, 1), round(v / 100, 4)}
    return out


def check(text: str, facts: dict, tests: list[TestCase], cat: Catalog) -> list[str]:
    problems = []
    known_ids = set(cat.rules) | {t.id for t in tests} | {c["id"] for c in facts.get("conflicts", [])}
    for i in sorted(set(ID_RE.findall(text))):
        if i not in known_ids:
            problems.append(f"Mentions {i}, which does not exist.")
    allowed = _numbers_in(facts) | {float(n) for n in range(0, 11)}
    allowed |= {float(round(a)) for a in allowed} | {round(a, 1) for a in allowed}
    for m in NUM_RE.finditer(ID_RE.sub("", text)):
        raw = m.group(1).replace(",", "")
        try:
            v = float(raw)
        except ValueError:
            continue
        if v not in allowed and round(v, 1) not in allowed:
            problems.append(f"States the number {m.group(0)}, which is not in the computed facts.")
    blocking = facts.get("failures") or any(c["severity"] == "high" for c in facts.get("conflicts", []))
    if blocking and POSITIVE.search(text):
        problems.append("Recommends release although tests fail or high-severity conflicts are open.")
    return problems


def review(llm: LLM, facts: dict, tests: list[TestCase], cat: Catalog) -> tuple[str, dict]:
    log = {"attempts": [], "final": ""}
    if llm.offline:
        text = _offline_explain(facts)
        log["attempts"].append({"source": "template", "problems": check(text, facts, tests, cat)})
        log["final"] = "template"
        return text, log
    feedback = None
    for attempt in range(2):
        text = llm.explain(facts, feedback=feedback)
        problems = check(text, facts, tests, cat)
        log["attempts"].append({"source": "writer", "problems": problems})
        if not problems:
            log["final"] = "writer" if attempt == 0 else "writer after rewrite"
            return text, log
        feedback = "Fix these problems and rewrite the report:\n- " + "\n- ".join(problems)
    log["final"] = "template (writer failed the critic twice)"
    return _offline_explain(facts), log
