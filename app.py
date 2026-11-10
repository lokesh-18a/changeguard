"""ChangeGuard web interface.  Run:  streamlit run app.py"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st

import random

from changeguard import mutation
from changeguard.catalog import Catalog, apply_change
from changeguard.export import to_excel
from changeguard.impact import build_graph
from changeguard.pipeline import run

st.set_page_config(page_title="ChangeGuard", page_icon="🛡️", layout="wide")

EXAMPLES = {
    "New add-on: Zero Depreciation": (
        "Introduce a Zero Depreciation add-on for private cars up to 5 years old, priced at 15% of the "
        "own-damage premium, limited to 2 claims per policy year. Customers can add it mid-term."),
    "Parameter change: higher deductible": (
        "Raise the compulsory deductible for cars from 1,000 to 1,500 cc to Rs 1,500"),
}
AREA_ORDER = ["vehicle", "rating", "underwriting", "billing", "claims", "renewal", "documents"]

# ---------------- sidebar ----------------
with st.sidebar:
    st.header("Settings")
    offline = st.toggle("Offline mode (no AWS)", value=False,
                        help="Uses the rule-based stub instead of Amazon Bedrock for the two AI steps.")
    st.caption("AI steps: Amazon Bedrock, Amazon Nova Lite. Everything else is deterministic code.")
    st.divider()
    st.subheader("System under test")
    st.caption("The system has no planted bugs. Test strength is measured by injecting random small "
               "defects (mutants) and counting how many the tests catch.")
    st.divider()
    st.caption("Product: fictional private car insurance, 23 rules. Numbers are illustrative.")

# ---------------- header and input ----------------
st.title("ChangeGuard")
st.markdown("**Change-to-verdict copilot for insurance delivery teams.** Describe a product change; "
            "get every affected rule, the conflicts, generated tests and their results.")

choice = st.radio("Example", list(EXAMPLES), horizontal=True, label_visibility="collapsed")
request = st.text_area("Change request", EXAMPLES[choice], height=90)
go = st.button("Analyze change", type="primary")

if go:
    with st.status("Running agents...", expanded=False) as status:
        st.write("1. Understanding the change")
        rep = run(request, offline=offline)
        st.write("2. Impact and conflicts  ·  3. Test design  ·  4. Test run  ·  5. Explanation")
        status.update(label="Done", state="complete")
    st.session_state["rep"] = rep

rep = st.session_state.get("rep")
if not rep:
    st.info("Pick an example or write a change, then press **Analyze change**.")
    st.stop()

# ---------------- headline numbers ----------------
failed = len(rep.results) - rep.passed
stale = sum(t.stale for t in rep.tests)
high = sum(c.severity == "high" for c in rep.conflicts)
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Rules affected", len(rep.impact))
c2.metric("Conflicts", len(rep.conflicts), f"{high} high" if high else None, delta_color="inverse")
c3.metric("Tests passed", f"{rep.passed}/{len(rep.results)}", f"{failed} failed" if failed else "all pass",
          delta_color="inverse" if failed else "normal")
c4.metric("Stale old tests", stale)
if rep.mutation:
    c5.metric("Random defects caught", f"{rep.mutation['score']:.0%}",
              f"typical suite {rep.mutation['baseline_score']:.0%}", delta_color="off")

verdict_bad = failed or high
(st.error if verdict_bad else st.success)(
    "**Verdict: do not release yet.** Fix the failures and high conflicts below." if verdict_bad
    else "**Verdict: ready for analyst sign-off.**")

with st.expander("How the change was understood (AI step 1)"):
    st.json(rep.change.model_dump(exclude_none=True))

tab_imp, tab_con, tab_test, tab_mut, tab_trace, tab_rep, tab_src, tab_ai = st.tabs(
    ["Impact", "Conflicts", "Tests", "Test strength", "Traceability", "Report", "Real sources", "AI usage"])

# ---------------- impact ----------------
with tab_imp:
    cat, _, _ = apply_change(Catalog.load(), rep.change)
    g = build_graph(cat)
    status_of = {i.rule_id: i.status for i in rep.impact}
    conflict_rules = {r for c in rep.conflicts for r in c.rules}
    lines = ["digraph G {", "rankdir=LR; bgcolor=transparent; node [shape=box style=\"rounded,filled\" fontname=Helvetica fontsize=10];",
             "edge [color=\"#9aa0a6\" arrowsize=0.6];"]
    for area in AREA_ORDER:
        nodes = [n for n in g.nodes if g.nodes[n]["area"] == area]
        if not nodes:
            continue
        lines.append(f'subgraph cluster_{area} {{ label="{area}"; style=dashed; color="#c0c4c8"; fontname=Helvetica; fontsize=11;')
        for n in nodes:
            s = status_of.get(n)
            fill = {"new": "#cfe2ff", "changed": "#cfe2ff", "affected": "#fff3cd"}.get(s, "#f1f3f4")
            pen = "#d93025" if n in conflict_rules else ("#1a73e8" if s in ("new", "changed") else "#bdc1c6")
            width = 2.2 if n in conflict_rules or s in ("new", "changed") else 1
            lines.append(f'"{n}" [fillcolor="{fill}" color="{pen}" penwidth={width}];')
        lines.append("}")
    for a, b in g.edges:
        hot = a in status_of and b in status_of
        lines.append(f'"{a}" -> "{b}" [color="{"#e37400" if hot else "#dadce0"}" penwidth={1.6 if hot else 0.8}];')
    lines.append("}")
    st.graphviz_chart("\n".join(lines), width="stretch")
    st.caption("Blue = new or changed rule · Amber = affected · Red border = involved in a conflict · "
               "Arrows = data flowing from one rule to another")
    st.dataframe(pd.DataFrame([{"Rule": i.rule_id, "Area": i.area, "Name": i.name, "Status": i.status,
                                "Hops": i.distance, "Why": rep.paths[i.rule_id]} for i in rep.impact]),
                 width="stretch", hide_index=True)

# ---------------- conflicts ----------------
with tab_con:
    if not rep.conflicts:
        st.success("No conflicts with existing rules.")
    for c in rep.conflicts:
        box = st.error if c.severity == "high" else st.warning
        box(f"**{c.id} · {c.severity}** — {c.message}\n\n*Fix:* {c.fix}\n\n*Rules:* {', '.join(c.rules)}")

# ---------------- tests ----------------
with tab_test:
    res = {r.test_id: r for r in rep.results}
    rows = [{"ID": t.id, "Result": "PASS" if res[t.id].passed else "FAIL", "Title": t.title,
             "Technique": t.technique, "Req": t.requirement, "Origin": t.origin,
             "Stale": "yes" if t.stale else "", "Note": res[t.id].note} for t in rep.tests]
    df = pd.DataFrame(rows)
    only_fail = st.toggle("Show failures and stale tests only")
    if only_fail:
        df = df[(df["Result"] == "FAIL") | (df["Stale"] == "yes")]
    st.dataframe(df.style.map(lambda v: "background-color:#fde2e1" if v == "FAIL" else
                              ("background-color:#e6f4ea" if v == "PASS" else ""), subset=["Result"]),
                 width="stretch", hide_index=True)
    st.caption("Expected results come from the oracle (plain code that reads the rule catalog), never from the AI.")

# ---------------- mutation / random defects ----------------
with tab_mut:
    m = rep.mutation
    if not m:
        st.info("Mutation audit was not run.")
    else:
        st.markdown(f"**{m['killed']} of {m['mutants']}** random defects caught by ChangeGuard's {m['tests']} tests "
                    f"(**{m['score']:.0%}**), against **{m['baseline_killed']}** ({m['baseline_score']:.0%}) for a "
                    f"typical suite of {m['baseline_tests']} tests (one ordinary example per rule, plus the existing "
                    f"regression tests). {len(m['only_ours'])} defects were caught only by ChangeGuard.")
        st.subheader("Try it: inject one random defect")
        cat_now, _, _ = apply_change(Catalog.load(), rep.change)
        if st.button("Inject a random defect and run the tests"):
            pick = random.choice(mutation.all_mutants())
            res = mutation.audit(cat_now, rep.tests, [pick])
            failing = res.killers.get(pick.id, [])
            src_line = mutation.SOURCE.splitlines()[pick.line - 1].strip()
            st.code(f"engine.py {pick.description}\n    {src_line}", language="text")
            (st.success if failing else st.warning)(
                f"Caught by {len(failing)} test(s): {', '.join(failing[:10])}" if failing
                else "Not caught. Either the tests miss it, or this defect changes nothing observable "
                     "(an 'equivalent mutant', e.g. a default value that is never used).")
        st.dataframe(pd.DataFrame([{"Mutant": k, "Kind": m["kinds"][k], "Defect": m["descriptions"][k],
                                    "ChangeGuard": "caught" if k in m["killers"] else "missed",
                                    "Typical suite": "caught" if k in m["baseline_killers"] else "missed"}
                                   for k in sorted(m["descriptions"])]), width="stretch", hide_index=True)

# ---------------- traceability ----------------
with tab_trace:
    trows = []
    for rid, text in rep.requirements.items():
        ts = [t for t in rep.tests if t.requirement == rid]
        trows.append({"Requirement": rid, "Text": text, "Tests": ", ".join(t.id for t in ts),
                      "Status": f"{sum(res[t.id].passed for t in ts)}/{len(ts)} passed" if ts else "NO TESTS"})
    st.dataframe(pd.DataFrame(trows), width="stretch", hide_index=True)

# ---------------- report ----------------
with tab_rep:
    st.caption(f"Checked by the code critic: {rep.critic.get('final', '')}. Every ID it mentions exists and every "
               "number it states comes from the computed results.")
    st.markdown(rep.explanation)

# ---------------- real sources ----------------
with tab_src:
    st.markdown("The catalog's numbers are checked against real insurer wordings (pipeline A).")
    kr = Path("out/knowledge_report.json")
    if kr.exists():
        import json as _json
        rows = _json.loads(kr.read_text())[0]["catalog_check"]
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    else:
        st.info("Run `python -m changeguard.knowledge.build` once to check the catalog against the source documents.")

# ---------------- AI usage ----------------
with tab_ai:
    st.caption(f"Backend: {rep.backend}")
    st.dataframe(pd.DataFrame(rep.llm_calls), width="stretch", hide_index=True)
    tin = sum(c["input_tokens"] for c in rep.llm_calls)
    tout = sum(c["output_tokens"] for c in rep.llm_calls)
    st.write(f"Total tokens: {tin:,} in, {tout:,} out.")

# ---------------- download ----------------
with tempfile.TemporaryDirectory() as d:
    path = to_excel(rep, Path(d) / "changeguard_results.xlsx")
    st.download_button("Download evidence pack (Excel)", path.read_bytes(), "changeguard_results.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
