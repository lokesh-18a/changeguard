# ChangeGuard

**A change-to-verdict copilot for insurance delivery teams.** Describe a product change in plain English. ChangeGuard finds every rule it affects, flags conflicts, generates the tests, runs them, and hands back the evidence for sign-off.

Built for the Flarre AI Innovation Challenge 2026.

## What it does

```
Change request (plain English)
  1. Understand   - language model turns it into a structured change      (Amazon Bedrock, Nova)
  2. Find impact  - walks the rule graph, lists affected rules + why      (code)
     Conflicts    - checks existing rules can cope with the change        (code)
  3. Design tests - boundary values, equivalence classes, exclusions;     (code)
                    expected results from a deterministic oracle
  4. Run tests    - runs them on the system; re-checks old regression     (code)
                    tests and flags the ones the change made stale
  5. Explain      - short report for the analyst                          (Amazon Bedrock, Nova)
  -> Human sign-off
```

Only steps 1 and 5 use the language model. Every answer that decides pass or fail comes from code, so the same change always gives the same verdict.

## Quick start

```bash
pip install -r requirements.txt
python -m changeguard.cli --offline          # runs the Zero Depreciation demo without AWS
pytest -q                                    # unit tests
```

With AWS set up (`aws configure`, region `us-east-1`, Amazon Nova access):

```bash
python scripts/check_bedrock.py              # one test call
python -m changeguard.cli "Introduce a Zero Depreciation add-on for cars up to 5 years old, 15% of OD premium, max 2 claims a year, can be added mid-term"
```

Outputs go to `out/`: `changeguard_results.xlsx` (summary, impact, conflicts, tests, traceability, AI usage) and `changeguard_report.md`.

## The demo product

`data/rules.json` is a fictional private car insurance product with 23 rules across rating, underwriting, billing, claims, renewal and documents. Numbers are illustrative, modelled on common Indian motor insurance practice. Rules link through the data fields they read and write, so the dependency graph is built automatically.

`data/regression_tests.json` is the "existing" regression suite (8 tests) with expected values from product version 1.0.

## Measuring test strength (no planted bugs)

The system under test (`changeguard/engine.py`) has no deliberate bugs. Instead, `changeguard/mutation.py`
creates every possible small defect in it automatically (comparison, arithmetic, constant, boolean and
rounding slips: 66 mutants) and checks how many the generated tests catch. `--mutants N --seed S` runs a
random sample instead. The same audit runs on a "typical" suite (one ordinary example per rule plus the
existing regression tests) for comparison.

## Pipeline A: real documents

```bash
python scripts/fetch_sources.py                         # downloads the insurer wordings listed in data/sources.json
python -m changeguard.knowledge.build                   # rule-based baseline extractor, no AWS needed
python -m changeguard.knowledge.build --extractor both  # baseline vs Amazon Nova, scored against data/gold_facts.json
```

Every extracted fact must quote the document word for word; the grounding verifier rejects anything it
cannot find, and the reconciler flags contradictions inside a document and differences between insurers.

## Benchmarks

```bash
python -m changeguard.benchmark --offline   # 12 labelled change requests, rule-based parser
python -m changeguard.benchmark --k 3       # Amazon Nova, 3 runs each: accuracy and pass^3
```

## Demo scenarios

1. **New add-on (Zero Depreciation):** 15 rules affected, 5 conflicts (including a refund rule that the dependency graph alone misses), 26 tests, 4 failures pinpointing both bugs.
2. **Parameter change (deductible Rs 1,000 to Rs 1,500):** 5 rules affected, and 2 old regression tests flagged as stale because their expected payouts are now wrong.

## Project layout

| File | Role |
|---|---|
| `changeguard/models.py` | Typed data passed between agents (Pydantic) |
| `changeguard/catalog.py` | Loads rules; applies a structured change |
| `changeguard/impact.py` | Rule graph (NetworkX), impact paths, conflict checks |
| `changeguard/oracle.py` | Reference calculation of the correct answer |
| `changeguard/engine.py` | System under test, with planted bugs |
| `changeguard/testgen.py` | Test design and stale-test detection |
| `changeguard/runner.py` | Runs tests, compares with the oracle |
| `changeguard/llm.py` | Amazon Bedrock (Nova) calls, with offline fallback and token logging |
| `changeguard/pipeline.py` | Orchestrates the five agents |
| `changeguard/export.py` | Excel and Markdown evidence pack |

## Roadmap

- Docker image and AWS deployment
- Extract the rule graph from real Guidewire or Duck Creek configuration
