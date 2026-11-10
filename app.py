"""ChangeGuard web interface.

Local:   streamlit run app.py
Hosted:  Streamlit Community Cloud. AWS keys go in the app's Secrets (never in the repo):
         AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION = "us-east-1"
"""
from __future__ import annotations

import json
import os
import random
import tempfile
from datetime import date
from html import escape
from pathlib import Path

import pandas as pd
import streamlit as st

st.set_page_config(page_title="ChangeGuard · change-to-verdict copilot", page_icon="🛡️", layout="wide")


def _load_secrets() -> None:
    """On Streamlit Cloud the AWS keys arrive as app secrets; boto3 reads them from the environment."""
    try:
        for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION", "AWS_DEFAULT_REGION"):
            if k in st.secrets and not os.getenv(k):
                os.environ[k] = str(st.secrets[k])
    except Exception:  # no secrets file locally: fall back to the normal AWS config
        pass


_load_secrets()

from changeguard import mutation  # noqa: E402  (imported after the environment is set)
from changeguard.catalog import Catalog, apply_change  # noqa: E402
from changeguard.export import to_excel  # noqa: E402
from changeguard.impact import build_graph  # noqa: E402
from changeguard.pipeline import run  # noqa: E402

REPO_URL = "https://github.com/lokesh-18a/changeguard"
ROOT = Path(__file__).parent
MEASURED = ROOT / "data" / "measured"
SESSION_CAP, DAILY_CAP, MAX_CHARS = 10, 300, 600   # protects the AWS budget on a public site

EXAMPLES = {
    "New add-on: Zero Depreciation": (
        "Introduce a Zero Depreciation add-on for private cars up to 5 years old, priced at 15% of the "
        "own-damage premium, limited to 2 claims per policy year. Customers can add it mid-term."),
    "Parameter change: higher deductible": (
        "Raise the compulsory deductible for cars from 1,000 to 1,500 cc to Rs 1,500"),
}
AREA_ORDER = ["vehicle", "rating", "underwriting", "billing", "claims", "renewal", "documents"]
AGENTS = [  # (name in pipeline trace, display name, kind, what it does)
    ("Planner", "Planner", "Nova", "Turns the request into a typed change"),
    ("Impact", "Impact analyst", "code", "Walks the rule data-flow graph"),
    ("Conflict", "Conflict checker", "code", "Finds rules that cannot cope"),
    ("Test designer", "Test designer", "code", "Boundaries, classes, exclusions"),
    ("Oracle", "Oracle + runner", "code", "Expected values from the catalog"),
    ("Mutation", "Mutation auditor", "code", "Injects random defects"),
    ("Writer", "Writer + Critic", "Nova + code", "Report; every number checked"),
]

# ---------------- styling ----------------
st.markdown("""
<style>
.block-container {padding-top: 1.6rem; max-width: 1240px;}
.hero {background: linear-gradient(120deg, #0E1A2B 0%, #12324A 60%, #0B5D5A 100%); color: #F3F6FA;
       border-radius: 16px; padding: 28px 32px 24px; margin-bottom: 18px;}
.hero .eyebrow {font-size: .78rem; letter-spacing: .08em; text-transform: uppercase; color: #8FD3CC; font-weight: 600;}
.hero .title {font-size: 2.5rem; font-weight: 800; margin: 4px 0 6px; color: #FFFFFF; line-height: 1.1;}
.hero .lead {font-size: 1.06rem; max-width: 820px; color: #D6DEE8; margin: 0 0 14px;}
.hero .badges {display: flex; flex-wrap: wrap; gap: 8px;}
.hero .badges a, .hero .badges span {font-size: .8rem; padding: 5px 11px; border-radius: 999px;
       border: 1px solid rgba(255,255,255,.25); color: #F3F6FA !important; text-decoration: none; background: rgba(255,255,255,.06);}
.hero .badges a.primary {background: #F3F6FA; color: #0E1A2B !important; font-weight: 700; border-color: #F3F6FA;}
.pipe {display: grid; grid-template-columns: repeat(7, minmax(0, 1fr)); gap: 8px; margin: 6px 0 14px;}
@media (max-width: 900px) {.pipe {grid-template-columns: repeat(2, minmax(0, 1fr));} .hero .title {font-size: 2rem;}}
.agent {background: #FFFFFF; border: 1px solid #E1E6EC; border-radius: 12px; padding: 10px 11px; position: relative;}
.agent .n {font-size: .7rem; color: #6B7686; font-weight: 600;}
.agent .name {font-weight: 700; font-size: .92rem; color: #14213D; margin: 1px 0 4px;}
.agent .kind {display: inline-block; font-size: .68rem; font-weight: 700; padding: 1px 7px; border-radius: 999px;}
.agent .kind.ai {background: #E6F3F2; color: #0B6E69;}
.agent .kind.code {background: #EEF0F4; color: #4A5568;}
.agent .what {font-size: .78rem; color: #4A5568; margin-top: 6px; line-height: 1.3;}
.agent .out {font-size: .86rem; color: #0E1A2B; font-weight: 700; margin-top: 6px;}
.agent .t {font-size: .7rem; color: #8A94A3;}
.agent.done {border-color: #9ED3CE;}
.verdict {border-radius: 14px; padding: 16px 20px; margin: 4px 0 14px; display: flex; gap: 14px; align-items: center;}
.verdict.bad {background: #FDECEC; border: 1px solid #F3B9B6;}
.verdict.good {background: #E8F5EE; border: 1px solid #A9D9BC;}
.verdict .big {font-size: 1.25rem; font-weight: 800;}
.verdict.bad .big {color: #B42318;} .verdict.good .big {color: #1E7A46;}
.verdict .sub {color: #3F4A5A; font-size: .92rem;}
div[data-testid="stMetric"] {background: #FFFFFF; border: 1px solid #E1E6EC; border-radius: 12px; padding: 12px 16px;}
.stat {background: #FFFFFF; border: 1px solid #E1E6EC; border-radius: 14px; padding: 16px 18px; height: 100%;}
.stat .v {font-size: 2.1rem; font-weight: 800; color: #0B6E69; line-height: 1.1;}
.stat .l {font-size: .9rem; font-weight: 700; color: #14213D; margin-top: 4px;}
.stat .s {font-size: .8rem; color: #5B6575; margin-top: 2px;}
.bars {margin: 4px 0 16px;}
.bar {display: grid; grid-template-columns: 210px 1fr 54px; gap: 10px; align-items: center; margin: 6px 0;}
.bar .lab {font-size: .86rem; color: #2D3748;}
.bar .track {background: #E8ECF1; border-radius: 6px; height: 14px; overflow: hidden;}
.bar .fill {height: 100%; border-radius: 6px;}
.bar .val {font-size: .86rem; font-weight: 700; color: #14213D; text-align: right;}
@media (max-width: 700px) {.bar {grid-template-columns: 120px 1fr 46px;}}
.quote {border-left: 3px solid #E0A100; background: #FFF8E6; padding: 10px 14px; border-radius: 6px; font-size: .88rem; margin: 6px 0;}
</style>
""", unsafe_allow_html=True)


# ---------------- helpers ----------------
@st.cache_resource
def aws_ready() -> bool:
    if os.getenv("CHANGEGUARD_OFFLINE") == "1":
        return False
    try:
        import boto3
        return boto3.Session().get_credentials() is not None
    except Exception:
        return False


@st.cache_resource
def _daily_usage() -> dict:
    return {"day": "", "runs": 0}


def nova_budget_left() -> int:
    u = _daily_usage()
    if u["day"] != date.today().isoformat():
        u["day"], u["runs"] = date.today().isoformat(), 0
    return min(SESSION_CAP - st.session_state.get("nova_runs", 0), DAILY_CAP - u["runs"])


def spend_nova_run() -> None:
    st.session_state["nova_runs"] = st.session_state.get("nova_runs", 0) + 1
    _daily_usage()["runs"] += 1


@st.cache_resource
def reference_run():
    """The Zero Depreciation example with the rule-based parser: used for the landing numbers."""
    return run(EXAMPLES["New add-on: Zero Depreciation"], offline=True)


@st.cache_data
def measured(name: str):
    p = MEASURED / name
    return json.loads(p.read_text()) if p.exists() else None


def pct(v: float) -> str:
    """Same rounding as the proposal and deck: two decimals, then a percentage."""
    return f"{round(v, 2):.0%}"


def stat(value: str, label: str, sub: str) -> str:
    return f'<div class="stat"><div class="v">{value}</div><div class="l">{label}</div><div class="s">{sub}</div></div>'


def bars(rows: list[tuple[str, float, str]]) -> str:
    out = ['<div class="bars">']
    for label, v, colour in rows:
        out.append(f'<div class="bar"><div class="lab">{escape(label)}</div><div class="track">'
                   f'<div class="fill" style="width:{max(v, 0.005) * 100:.1f}%;background:{colour}"></div></div>'
                   f'<div class="val">{pct(v)}</div></div>')
    return "".join(out) + "</div>"


def _secs(t: float) -> str:
    return "under 10 ms" if t < 0.01 else f"{t:.2f} s"


def pipeline_strip(rep=None) -> str:
    timing, result = {}, {}
    if rep is not None:
        for t in rep.trace:
            for key, *_ in AGENTS:
                if t["agent"].startswith(key):
                    timing[key] = t["seconds"]
        failed = len(rep.results) - rep.passed
        high = sum(c.severity == "high" for c in rep.conflicts)
        result = {
            "Planner": rep.change.kind.replace("_", " "),
            "Impact": f"{len(rep.impact)} rules hit",
            "Conflict": f"{len(rep.conflicts)} found · {high} high",
            "Test designer": f"{len(rep.tests)} tests",
            "Oracle": f"{rep.passed}/{len(rep.results)} pass" + (f" · {failed} fail" if failed else ""),
            "Mutation": f"{pct(rep.mutation['score'])} defects caught" if rep.mutation else "skipped",
            "Writer": {"writer": "report approved", "writer after rewrite": "approved after 1 rewrite",
                       "template": "template report"}.get(rep.critic.get("final", ""), "template report"),
        }
    cards = []
    for i, (key, name, kind, what) in enumerate(AGENTS, 1):
        cls = "ai" if "Nova" in kind else "code"
        body = (f'<div class="out">{escape(result[key])}</div><div class="t">{_secs(timing.get(key, 0))}</div>'
                if key in result else f'<div class="what">{what}</div>')
        cards.append(f'<div class="agent{" done" if result else ""}"><div class="n">STEP {i}</div>'
                     f'<div class="name">{name}</div><span class="kind {cls}">{kind}</span>{body}</div>')
    return '<div class="pipe">' + "".join(cards) + "</div>"


# ---------------- sidebar ----------------
with st.sidebar:
    st.markdown("### Settings")
    can_use_nova = aws_ready()
    use_nova = st.toggle("Use Amazon Nova (Bedrock)", value=can_use_nova, disabled=not can_use_nova,
                         help="Nova Lite understands the request and writes the report. "
                              "Off = a rule-based parser that only knows simple wordings.")
    if not can_use_nova:
        st.caption("No AWS credentials found, so the rule-based parser is used.")
    elif use_nova:
        st.caption(f"Nova runs left in this session: {max(nova_budget_left(), 0)}")
    st.divider()
    st.markdown("**System under test**")
    st.caption("A fictional private-car insurance product: 23 rules, numbers checked against real insurer "
               "wordings. The engine has no planted bugs; test strength is measured by injecting random defects.")
    st.divider()
    st.link_button("Source code on GitHub", REPO_URL, width="stretch")

# ---------------- hero ----------------
st.markdown(f"""
<div class="hero">
  <div class="eyebrow">Flarre AI Innovation Challenge 2026 · PS1 change impact + PS2 testing</div>
  <div class="title">ChangeGuard</div>
  <p class="lead">A change-to-verdict copilot for Flarre's delivery teams. Describe an insurance product change in
  plain English: ChangeGuard finds every rule it touches, flags conflicts, writes and runs the tests, proves the
  tests catch random defects, and hands back an evidence pack for sign-off.</p>
  <div class="badges">
    <a class="primary" href="{REPO_URL}" target="_blank">GitHub repository ↗</a>
    <span>Amazon Nova on Bedrock</span><span>7 agents · typed contracts</span>
    <span>MCP server</span><span>Tests in GitHub Actions</span>
  </div>
</div>
""", unsafe_allow_html=True)

page_run, page_results, page_how = st.tabs(["▶  Run a change", "📊  Measured results", "🧭  How it works"])

# ======================================================================
with page_run:
    choice = st.segmented_control("Example", list(EXAMPLES), default=list(EXAMPLES)[0],
                                  label_visibility="collapsed") or list(EXAMPLES)[0]
    request = st.text_area("Change request (or write your own)", EXAMPLES[choice], height=96,
                           max_chars=MAX_CHARS, key=f"req_{choice}")
    go = st.button("Analyze change", type="primary", icon="🛡️")

    if go and request.strip():
        nova = use_nova and nova_budget_left() > 0
        if use_nova and not nova:
            st.warning("Nova limit reached for this demo, so the rule-based parser is used for this run.")
        with st.status("Agents at work…", expanded=False) as status:
            rep = run(request, offline=not nova)
            if nova:
                spend_nova_run()
            status.update(label=f"Done in {sum(t['seconds'] for t in rep.trace):.1f}s · {rep.backend}",
                          state="complete")
        st.session_state["rep"] = rep

    rep = st.session_state.get("rep")
    st.markdown(pipeline_strip(rep), unsafe_allow_html=True)

    if not rep:
        ref = reference_run()
        bn, bo = measured("benchmark_nova.json"), measured("benchmark_offline.json")
        c = st.columns(4)
        c[0].markdown(stat(f"{pct(ref.mutation['score'])}", "random defects caught",
                           f"vs {pct(ref.mutation['baseline_score'])} for a typical test suite"), unsafe_allow_html=True)
        if bn:
            c[1].markdown(stat(f"{pct(bn['summary']['impact_full_recall'])}", "impact recall",
                               f"on 12 labelled changes · graph alone {pct(bn['summary']['impact_graph_recall'])}"),
                          unsafe_allow_html=True)
            c[2].markdown(stat(f"{pct(bn['summary']['pass_hat_k'])}", "Nova understanding, pass^3",
                               f"36/36 runs · rule-based parser {pct(bo['summary']['understanding_accuracy'])}"
                               if bo else "36/36 runs"), unsafe_allow_html=True)
        kr = measured("knowledge_report.json")
        if kr:
            hyb = next((r for r in kr if r["extractor"] == "hybrid"), None)
            if hyb:
                o = hyb["score_vs_gold"]["overall"]
                c[3].markdown(stat(f"{pct(o['precision'])}", "facts read correctly from real PDFs",
                                   f"recall {pct(o['recall'])} on {o['gold']} hand-labelled facts"),
                              unsafe_allow_html=True)
        st.write("")
        st.info("Pick an example or write your own change, then press **Analyze change**. "
                "A run takes a few seconds; all numbers above are reproducible from the repository.")
        st.stop()

    # ---------------- headline ----------------
    failed = len(rep.results) - rep.passed
    stale = sum(t.stale for t in rep.tests)
    high = sum(c.severity == "high" for c in rep.conflicts)
    verdict_bad = failed or high
    reasons = ([f"{failed} test(s) fail"] if failed else []) + ([f"{high} high-severity conflict(s)"] if high else [])
    st.markdown(
        f'<div class="verdict {"bad" if verdict_bad else "good"}"><div>'
        f'<div class="big">{"Do not release yet" if verdict_bad else "Ready for analyst sign-off"}</div>'
        f'<div class="sub">{escape(rep.change.summary)}'
        f'{" — " + ", ".join(reasons) + ". Details below." if reasons else ""}</div></div></div>',
        unsafe_allow_html=True)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Rules affected", len(rep.impact))
    c2.metric("Conflicts", len(rep.conflicts), f"{high} high" if high else None, delta_color="inverse")
    c3.metric("Tests passed", f"{rep.passed}/{len(rep.results)}", f"{failed} failed" if failed else "all pass",
              delta_color="inverse" if failed else "normal")
    c4.metric("Stale old tests", stale)
    if rep.mutation:
        c5.metric("Random defects caught", f"{pct(rep.mutation['score'])}",
                  f"typical suite {pct(rep.mutation['baseline_score'])}", delta_color="off")

    with st.expander("How the change was understood (Planner output, validated against the catalog)"):
        st.json(rep.change.model_dump(exclude_none=True))

    tab_imp, tab_con, tab_test, tab_mut, tab_trace, tab_rep, tab_ai = st.tabs(
        ["Impact graph", "Conflicts", "Tests", "Test strength", "Traceability", "Report", "AI usage"])

    with tab_imp:
        cat, _, _ = apply_change(Catalog.load(), rep.change)
        g = build_graph(cat)
        status_of = {i.rule_id: i.status for i in rep.impact}
        conflict_rules = {r for c in rep.conflicts for r in c.rules}
        lines = ["digraph G {", "rankdir=LR; bgcolor=transparent; node [shape=box style=\"rounded,filled\" "
                 "fontname=Helvetica fontsize=10];", "edge [color=\"#9aa0a6\" arrowsize=0.6];"]
        for area in AREA_ORDER:
            nodes = [n for n in g.nodes if g.nodes[n]["area"] == area]
            if not nodes:
                continue
            lines.append(f'subgraph cluster_{area} {{ label="{area}"; style=dashed; color="#c0c4c8"; '
                         f'fontname=Helvetica; fontsize=11;')
            for n in nodes:
                s = status_of.get(n)
                fill = {"new": "#CDEBE8", "changed": "#CDEBE8", "affected": "#FFF1C7"}.get(s, "#F1F3F4")
                pen = "#D93025" if n in conflict_rules else ("#0B7A75" if s in ("new", "changed") else "#BDC1C6")
                width = 2.2 if n in conflict_rules or s in ("new", "changed") else 1
                lines.append(f'"{n}" [fillcolor="{fill}" color="{pen}" penwidth={width}];')
            lines.append("}")
        for a, b in g.edges:
            hot = a in status_of and b in status_of
            lines.append(f'"{a}" -> "{b}" [color="{"#E37400" if hot else "#DADCE0"}" penwidth={1.6 if hot else 0.8}];')
        lines.append("}")
        st.graphviz_chart("\n".join(lines), width="stretch")
        st.caption("Teal = new or changed rule · Amber = affected · Red border = involved in a conflict · "
                   "Arrows = data flowing from one rule to another")
        st.dataframe(pd.DataFrame([{"Rule": i.rule_id, "Area": i.area, "Name": i.name, "Status": i.status,
                                    "Hops": i.distance, "Why": rep.paths[i.rule_id]} for i in rep.impact]),
                     width="stretch", hide_index=True)

    with tab_con:
        if not rep.conflicts:
            st.success("No conflicts with existing rules.")
        for c in rep.conflicts:
            box = st.error if c.severity == "high" else st.warning
            box(f"**{c.id} · {c.severity}** — {c.message}\n\n*Fix:* {c.fix}\n\n*Rules:* {', '.join(c.rules)}")

    res = {r.test_id: r for r in rep.results}
    with tab_test:
        rows = [{"ID": t.id, "Result": "PASS" if res[t.id].passed else "FAIL", "Title": t.title,
                 "Technique": t.technique, "Req": t.requirement, "Origin": t.origin,
                 "Stale": "yes" if t.stale else "", "Note": res[t.id].note} for t in rep.tests]
        df = pd.DataFrame(rows)
        if st.toggle("Show failures and stale tests only"):
            df = df[(df["Result"] == "FAIL") | (df["Stale"] == "yes")]
        st.dataframe(df.style.map(lambda v: "background-color:#fde2e1" if v == "FAIL" else
                                  ("background-color:#e6f4ea" if v == "PASS" else ""), subset=["Result"]),
                     width="stretch", hide_index=True)
        st.caption("Expected results come from the oracle (plain code that reads the rule catalog), never from the AI.")

    with tab_mut:
        m = rep.mutation
        if not m:
            st.info("Mutation audit was not run.")
        else:
            st.markdown(bars([("ChangeGuard's tests", m["score"], "#0B7A75"),
                              ("Typical suite", m["baseline_score"], "#A0AEC0")]), unsafe_allow_html=True)
            st.markdown(f"**{m['killed']} of {m['mutants']}** random defects caught by ChangeGuard's {m['tests']} tests, "
                        f"against **{m['baseline_killed']}** for a typical suite of {m['baseline_tests']} tests "
                        f"(one ordinary example per rule plus the existing regression tests). "
                        f"{len(m['only_ours'])} defects were caught only by ChangeGuard.")
            st.subheader("Try it: inject one random defect")
            cat_now, _, _ = apply_change(Catalog.load(), rep.change)
            if st.button("Inject a random defect and run the tests", icon="🎲"):
                pick = random.choice(mutation.all_mutants())
                audit = mutation.audit(cat_now, rep.tests, [pick])
                failing = audit.killers.get(pick.id, [])
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

    with tab_trace:
        trows = []
        for rid, text in rep.requirements.items():
            ts = [t for t in rep.tests if t.requirement == rid]
            trows.append({"Requirement": rid, "Text": text, "Tests": ", ".join(t.id for t in ts),
                          "Status": f"{sum(res[t.id].passed for t in ts)}/{len(ts)} passed" if ts else "NO TESTS"})
        st.dataframe(pd.DataFrame(trows), width="stretch", hide_index=True)

    with tab_rep:
        st.caption(f"Checked by the code critic: {rep.critic.get('final', '')}. Every ID it mentions exists and every "
                   "number it states comes from the computed results.")
        st.markdown(rep.explanation)

    with tab_ai:
        st.caption(f"Backend: {rep.backend}")
        st.dataframe(pd.DataFrame(rep.llm_calls), width="stretch", hide_index=True)
        tin = sum(c["input_tokens"] for c in rep.llm_calls)
        tout = sum(c["output_tokens"] for c in rep.llm_calls)
        st.write(f"Total tokens: {tin:,} in, {tout:,} out.")

    with tempfile.TemporaryDirectory() as d:
        path = to_excel(rep, Path(d) / "changeguard_results.xlsx")
        st.download_button("Download evidence pack (Excel)", path.read_bytes(), "changeguard_results.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           type="primary", icon="📥")

# ======================================================================
with page_results:
    st.markdown("Every number here is measured, not estimated, and can be reproduced with the commands in the "
                f"[README]({REPO_URL}#readme). The Nova results are from runs on Amazon Bedrock.")
    ref = reference_run()
    m = ref.mutation
    st.markdown("#### 1 · Are the generated tests strong? (random defects, no planted bugs)")
    st.markdown(bars([(f"ChangeGuard ({m['tests']} tests)", m["score"], "#0B7A75"),
                      (f"Typical suite ({m['baseline_tests']} tests)", m["baseline_score"], "#A0AEC0")]),
                unsafe_allow_html=True)
    st.caption(f"All {m['mutants']} small defects (mutants) of the pricing and claims engine: wrong comparisons, "
               "off-by-one constants, arithmetic and rounding slips. Zero Depreciation example.")

    bn, bo = measured("benchmark_nova.json"), measured("benchmark_offline.json")
    if bn:
        s = bn["summary"]
        st.markdown("#### 2 · Does it understand the change and find the impact? (12 labelled requests)")
        st.markdown(bars([("Nova: understood, all 3 runs", s["pass_hat_k"], "#0B7A75")] +
                         ([("Rule-based parser", bo["summary"]["understanding_accuracy"], "#A0AEC0")] if bo else []) +
                         [("Impact recall, graph + conflicts", s["impact_full_recall"], "#0B7A75"),
                          ("Impact recall, graph alone", s["impact_graph_recall"], "#A0AEC0"),
                          ("Impact precision", s["impact_full_precision"], "#2B6CB0")]), unsafe_allow_html=True)
        st.dataframe(pd.DataFrame([{"Request": r["id"], "Kind": r["kind"], "Nova runs correct": f"{r['runs_correct']}/{r['k']}",
                                    "Impact recall": r["full_recall"], "Impact precision": r["full_precision"],
                                    "Missed": ", ".join(r["missed"]), "Extra": ", ".join(r["extra"])}
                                   for r in bn["rows"]]),
                     width="stretch", hide_index=True,
                     column_config={"Impact recall": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1),
                                    "Impact precision": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1)})
        st.caption(f"Benchmark tokens: {s['tokens_in']:,} in, {s['tokens_out']:,} out for 36 Nova calls.")

    kr = measured("knowledge_report.json")
    if kr:
        st.markdown("#### 3 · Can it read real insurer documents? (5 public policy wordings, 85 hand-labelled facts)")
        rows = []
        for r in kr:
            o = r["score_vs_gold"]["overall"]
            name = {"baseline": "Patterns only", "nova": "Nova + verifier", "hybrid": "Hybrid"}[r["extractor"]]
            rows += [(f"{name}: precision", o["precision"], "#0B7A75" if r["extractor"] == "hybrid" else "#A0AEC0"),
                     (f"{name}: recall", o["recall"], "#2B6CB0" if r["extractor"] == "hybrid" else "#CBD5E0")]
        st.markdown(bars(rows), unsafe_allow_html=True)
        st.caption("Hybrid = patterns for tables (exact) + Nova for clause wording (patterns cannot read clauses). "
                   "Nova facts are only kept when the grounding verifier finds the quote in the PDF.")
        base = kr[0]
        for c in base["contradictions"]:
            st.markdown(f'<div class="quote"><b>Contradiction found in a real wording ({escape(c["doc"])}):</b> '
                        f'the NCB table lists {escape(c["key"])} claim-free years twice, with '
                        f'{" and ".join(escape(v) + "%" for v in c["values"])}.</div>', unsafe_allow_html=True)
        chk = base["catalog_check"]
        st.markdown(f"**Catalog check:** {sum(x['status'] == 'confirmed' for x in chk)} of {len(chk)} numbers in the "
                    "demo product match the real documents.")
        st.dataframe(pd.DataFrame([{"Rule": x["rule"], "Parameter": x["parameter"], "Catalog": x["catalog"],
                                    "Documents": x["documents"], "Status": x["status"],
                                    "Sources": ", ".join(x["sources"])} for x in chk]),
                     width="stretch", hide_index=True)
        nova = next((r for r in kr if r["extractor"] == "nova"), None)
        if nova and nova.get("tokens"):
            st.caption(f"Nova extraction: {nova['model_calls']} calls, {nova['tokens']['input']:,} tokens in, "
                       f"{nova['tokens']['output']:,} out. Every fact needs a verbatim quote that the verifier finds in the PDF.")

# ======================================================================
with page_how:
    st.image(str(ROOT / "docs" / "architecture.png"), width="stretch")
    left, right = st.columns(2)
    with left:
        st.markdown("""
**Pipeline A · knowledge build (offline, once per product)**
Real insurer PDFs → segmenter → extractor (patterns or Nova) → **grounding verifier** (every fact must quote the
document) → reconciler (contradictions, variations between insurers) → checks the rule catalog.

**Pipeline B · change run (seconds, per request)**
Planner (Nova) → impact analyst (rule data-flow graph) → conflict checker → test designer (boundary values,
equivalence classes, exclusions) → oracle + runner → **mutation auditor** → writer (Nova) → **critic** (code).
""")
    with right:
        st.markdown(f"""
**Why it can be trusted**
- Only two steps use the model; every pass/fail decision is plain code, so the same change gives the same verdict.
- The critic rejects any report that mentions an unknown rule or a number that was not computed.
- Test strength is proven with random defects, not claimed.

**Use it from other tools**
- MCP server: `python -m changeguard.mcp_server` (list rules, impact, tests, run, mutation audit)
- CLI: `python -m changeguard.cli --offline`
- Code, tests and CI: [{REPO_URL.replace('https://', '')}]({REPO_URL})
""")
