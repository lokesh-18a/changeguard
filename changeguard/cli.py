"""Command line: python -m changeguard.cli "Add a Zero Depreciation add-on ..." [--offline]"""
from __future__ import annotations

import argparse
from pathlib import Path

from .export import to_excel, to_markdown
from .pipeline import run

DEFAULT = ("Introduce a Zero Depreciation add-on for private cars up to 5 years old, priced at 15% of the "
           "own-damage premium, limited to 2 claims per policy year. Customers can add it mid-term.")


def main():
    ap = argparse.ArgumentParser(description="ChangeGuard: change-to-verdict copilot")
    ap.add_argument("request", nargs="?", default=DEFAULT)
    ap.add_argument("--offline", action="store_true", help="use the rule-based stub instead of Amazon Bedrock")
    ap.add_argument("--out", default="out")
    ap.add_argument("--mutants", type=int, default=None, help="random sample size; default: all mutants; 0: skip")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()

    rep = run(a.request, offline=a.offline, mutants=a.mutants, seed=a.seed)
    out = Path(a.out)
    xlsx = to_excel(rep, out / "changeguard_results.xlsx")
    md = out / "changeguard_report.md"
    md.write_text(to_markdown(rep))

    print(f"Backend: {rep.backend}")
    print(f"Understood: {rep.change.model_dump(exclude_none=True)}\n")
    print(f"IMPACT: {len(rep.impact)} rules")
    for i in rep.impact:
        print(f"  [{i.status:8}] {i.rule_id:18} {rep.paths[i.rule_id]}")
    print(f"\nCONFLICTS: {len(rep.conflicts)}")
    for c in rep.conflicts:
        print(f"  {c.id} ({c.severity}) {c.message}")
    print(f"\nTESTS: {rep.passed}/{len(rep.results)} passed")
    by = {t.id: t for t in rep.tests}
    for r in rep.results:
        t = by[r.test_id]
        flag = "PASS" if r.passed else "FAIL"
        print(f"  {flag} {t.id} {t.title}{'  [STALE]' if t.stale else ''}{'  -> ' + r.note if r.note else ''}")
    if rep.mutation:
        m = rep.mutation
        print(f"\nMUTATION AUDIT: {m['mutants']} random defects (seed {m['seed']}, from {m['total_points']} possible)")
        print(f"  ChangeGuard suite ({m['tests']} tests): caught {m['killed']} = {m['score']:.0%}")
        print(f"  Typical-case suite ({m['baseline_tests']} tests): caught {m['baseline_killed']} = {m['baseline_score']:.0%}")
        print(f"  Caught only by ChangeGuard: {len(m['only_ours'])}  |  survivors: {len(m['survivors'])}")
    print(f"\nCRITIC: {rep.critic.get('final')}  {[a['problems'] for a in rep.critic.get('attempts', [])]}")
    print("\nAI CALLS:")
    for c in rep.llm_calls:
        print(f"  {c['step']:18} {c['backend']:28} in={c['input_tokens']} out={c['output_tokens']} {c['seconds']}s")
    print(f"\n{rep.explanation}\n\nWrote {xlsx} and {md}")


if __name__ == "__main__":
    main()
