"""Writes the evidence pack: an Excel workbook and a Markdown report."""
from __future__ import annotations

import json
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from .pipeline import RunReport

HEAD = Font(bold=True, color="FFFFFF")
FILL = PatternFill("solid", fgColor="1F3A5F")
RED = PatternFill("solid", fgColor="F8D7DA")
GREEN = PatternFill("solid", fgColor="D4EDDA")
AMBER = PatternFill("solid", fgColor="FFF3CD")


def _sheet(wb, title, headers, rows, widths):
    ws = wb.create_sheet(title)
    ws.append(headers)
    for c in ws[1]:
        c.font, c.fill = HEAD, FILL
    for r in rows:
        ws.append(r)
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[ws.cell(1, i).column_letter].width = w
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "A2"
    return ws


def to_excel(rep: RunReport, path: Path) -> Path:
    wb = Workbook()
    wb.remove(wb.active)
    res = {r.test_id: r for r in rep.results}

    ws = _sheet(wb, "Summary", ["Item", "Value"], [
        ["Change request", rep.request_text],
        ["Understood as", rep.change.summary],
        ["Rules affected", len(rep.impact)],
        ["Conflicts", len(rep.conflicts)],
        ["Tests run", len(rep.results)],
        ["Passed", rep.passed],
        ["Failed", len(rep.results) - rep.passed],
        ["Stale regression tests", sum(t.stale for t in rep.tests)],
        ["Mutation score (all random defects)", f"{rep.mutation.get('score', 0):.0%} of {rep.mutation.get('mutants', 0)}" if rep.mutation else "not run"],
        ["Typical-case suite mutation score", f"{rep.mutation.get('baseline_score', 0):.0%}" if rep.mutation else "not run"],
        ["Report checked by critic", rep.critic.get("final", "")],
        ["Language model", rep.backend],
    ], [34, 90])

    _sheet(wb, "Impact", ["Rule", "Area", "Name", "Status", "Distance", "Why (dependency path)"],
           [[i.rule_id, i.area, i.name, i.status, i.distance, rep.paths[i.rule_id]] for i in rep.impact],
           [16, 14, 36, 10, 10, 90])
    ws = _sheet(wb, "Conflicts", ["ID", "Severity", "Rules", "Problem", "Suggested fix"],
                [[c.id, c.severity, ", ".join(c.rules), c.message, c.fix] for c in rep.conflicts], [6, 10, 34, 60, 60])
    for row in ws.iter_rows(min_row=2):
        row[1].fill = RED if row[1].value == "high" else AMBER

    ws = _sheet(wb, "Tests", ["ID", "Origin", "Title", "Technique", "Requirement", "Rules", "Inputs",
                              "Expected", "Actual", "Result", "Note"],
                [[t.id, t.origin, t.title, t.technique, t.requirement, ", ".join(t.rules),
                  json.dumps({k: v for k, v in t.inputs.items() if k != "parts"}),
                  json.dumps(t.expected), json.dumps(res[t.id].actual),
                  "PASS" if res[t.id].passed else "FAIL",
                  ("STALE: old expected " + json.dumps(t.old_expected) + ". " if t.stale else "") + res[t.id].note]
                 for t in rep.tests], [7, 11, 46, 18, 11, 30, 40, 34, 34, 8, 50])
    for row in ws.iter_rows(min_row=2):
        row[9].fill = GREEN if row[9].value == "PASS" else RED

    if rep.mutation:
        m = rep.mutation
        ws = _sheet(wb, "Mutation audit", ["Mutant", "Kind", "Defect injected", "Caught by ChangeGuard", "Caught by typical suite", "Caught by tests"],
                    [[mid, m["kinds"][mid], m["descriptions"][mid], "yes" if mid in m["killers"] else "NO",
                      "yes" if mid in m["baseline_killers"] else "no", ", ".join(m["killers"].get(mid, [])[:8])]
                     for mid in sorted(m["descriptions"])], [9, 12, 46, 20, 22, 60])
        for row in ws.iter_rows(min_row=2):
            row[3].fill = GREEN if row[3].value == "yes" else RED

    trace_rows = []
    for rid, text in rep.requirements.items():
        tests = [t for t in rep.tests if t.requirement == rid]
        rules = sorted({r for t in tests for r in t.rules})
        trace_rows.append([rid, text, ", ".join(rules), ", ".join(t.id for t in tests),
                           f"{sum(res[t.id].passed for t in tests)}/{len(tests)} passed" if tests else "NO TESTS"])
    _sheet(wb, "Traceability", ["Requirement (rule)", "Text", "Rules", "Tests", "Status"], trace_rows, [18, 60, 30, 40, 16])

    _sheet(wb, "AI usage", ["Step", "Backend", "Model", "Input tokens", "Output tokens", "Seconds"],
           [[c["step"], c["backend"], c["model"], c["input_tokens"], c["output_tokens"], c["seconds"]] for c in rep.llm_calls],
           [14, 12, 30, 14, 14, 10])
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def to_markdown(rep: RunReport) -> str:
    res = {r.test_id: r for r in rep.results}
    out = [f"# ChangeGuard report", "", f"**Change:** {rep.request_text}", "", rep.explanation, "",
           "## Impact", "", "| Rule | Area | Status | Why |", "|---|---|---|---|"]
    out += [f"| {i.rule_id} | {i.area} | {i.status} | `{rep.paths[i.rule_id]}` |" for i in rep.impact]
    out += ["", "## Conflicts", ""] + [f"- **{c.id} ({c.severity})** {c.message} *Fix:* {c.fix}" for c in rep.conflicts]
    out += ["", f"## Tests: {rep.passed}/{len(rep.results)} passed", "", "| ID | Title | Result | Note |", "|---|---|---|---|"]
    out += [f"| {t.id} | {t.title} | {'PASS' if res[t.id].passed else '**FAIL**'} | "
            f"{('stale; ' if t.stale else '') + res[t.id].note} |" for t in rep.tests]
    return "\n".join(out)
