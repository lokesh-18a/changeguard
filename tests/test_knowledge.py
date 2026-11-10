"""Pipeline A on the real documents. Skipped if the PDFs have not been fetched
(run: python scripts/fetch_sources.py)."""
from pathlib import Path

import pytest

from changeguard.catalog import DATA, Catalog
from changeguard.knowledge import build, extract
from changeguard.knowledge.facts import Fact, age_band
from changeguard.knowledge.reconcile import catalog_check
from changeguard.knowledge.verify import verify

HAVE_DOCS = (DATA / "sources" / "raheja_pc.pdf").exists()
needs_docs = pytest.mark.skipif(not HAVE_DOCS, reason="source PDFs not fetched")


def test_age_band_parsing():
    assert age_band("Not exceeding 6 months") == "0-0.5"
    assert age_band("Exceeding 6months but not exceeding 1 year") == "0.5-1"
    assert age_band("Exceeding 5 years but not exceeding 10 years") == "5-10"
    assert age_band("Exceeding 10 years") == "10+"


def test_verifier_rejects_invented_quotes_and_values():
    text = "Exceeding 1 year but not exceeding 2 years 10%"
    good = Fact(doc="d", type="metal_depreciation", key="1-2", value=10, quote=text)
    wrong_value = Fact(doc="d", type="metal_depreciation", key="1-2", value=15, quote=text)
    invented = Fact(doc="d", type="metal_depreciation", key="1-2", value=10, quote="depreciation is 10% for 1-2 years")
    bad_key = Fact(doc="d", type="metal_depreciation", key="one to two", value=10, quote=text)
    ok, rej = verify([good, wrong_value, invented, bad_key], {"d": "header\n" + text + "\nfooter"})
    assert ok == [good] and len(rej) == 3


@needs_docs
def test_baseline_on_real_wordings_and_catalog_confirmed():
    res = build.run("baseline")
    rep = build.report(res)
    assert rep["score_vs_gold"]["tables"]["precision"] == 1.0
    assert rep["score_vs_gold"]["tables"]["recall"] > 0.9
    assert any(c["doc"] == "raheja_pc" and c["type"] == "ncb" for c in rep["contradictions"])
    assert all(r["status"] == "confirmed" for r in rep["catalog_check"])


class FakeLLM:
    """Stands in for Nova: returns one true fact and one hallucinated fact per segment."""
    def __init__(self):
        self.calls = []

    def call_tool(self, step, system, text, tool, schema, max_tokens=0):
        if "Authorized Garage" in text:
            return {"facts": [
                {"type": "zero_dep_condition", "key": "authorised_garage_required", "value": "true",
                 "quote": "Vehicle is repaired at any of our Authorized Garage/ Authorized workshop/ Authorized"},
                {"type": "zero_dep_claim_limit", "key": "max_claims", "value": "3",
                 "quote": "This cover is available for a maximum of 3 claims"}]}
        return {"facts": []}


@needs_docs
def test_nova_path_with_verifier_and_retry():
    res = build.run("nova", docs=["raheja_pc"], llm=FakeLLM())
    keys = {(f.type, f.key, f.value) for f in res["accepted"]}
    assert ("zero_dep_condition", "authorised_garage_required", True) in keys
    assert not any(f.key == "max_claims" for f in res["accepted"])      # hallucination stopped
    assert any("quote does not appear" in r for _, r in res["rejected"])


def test_verifier_checks_what_the_quote_is_about():
    tyre = "Unused tread depth of >=7 mm - 100% of cost of new tyre and / or tube"
    as_rubber = Fact(doc="d", type="parts_depreciation", key="rubber_nylon_plastic", value=100, quote=tyre)
    word_value = Fact(doc="d", type="ncb", key="1", value="notwithstanding",
                      quote="Notwithstanding anything contained herein to the contrary, the No Claim Bonus")
    wrong_band = Fact(doc="d", type="idv_depreciation", key="1-2", value=15,
                      quote="Exceeding 6 months but not exceeding 1 year 15%")
    not_zero_dep = Fact(doc="d", type="zero_dep_condition", key="excludes_theft", value=True,
                        quote="Loss of keys due to theft is covered", section="Key Replacement cover")
    text = "\n".join([tyre, word_value.quote, wrong_band.quote, not_zero_dep.quote])
    ok, rej = verify([as_rubber, word_value, wrong_band, not_zero_dep], {"d": text})
    assert ok == [] and len(rej) == 4
