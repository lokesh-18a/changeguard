"""Exercises the Bedrock code path with a fake client (no AWS needed)."""
from changeguard import pipeline
from changeguard.llm import LLM


class FakeBedrock:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def converse(self, **kw):
        self.calls.append(kw)
        return self.replies.pop(0)


def tool_reply(data):
    return {"output": {"message": {"role": "assistant", "content": [{"toolUse": {"toolUseId": "t1", "name": "record_change", "input": data}}]}},
            "usage": {"inputTokens": 900, "outputTokens": 80}}


def text_reply(text):
    return {"output": {"message": {"role": "assistant", "content": [{"text": text}]}}, "usage": {"inputTokens": 500, "outputTokens": 150}}


def make_llm(replies):
    llm = LLM(offline=True)
    llm.offline, llm._client = False, FakeBedrock(replies)
    return llm


def test_parse_with_percent_slip_and_retry(monkeypatch):
    bad = {"kind": "new_addon", "addon_code": "zd", "max_car_age_years": 5, "price_pct_of_od": 15}   # missing claim limit
    good = {**bad, "claim_limit_per_year": 2, "allow_midterm": True}
    llm = make_llm([tool_reply(bad), tool_reply(good), text_reply("**Summary.** ok")])
    monkeypatch.setattr(pipeline, "LLM", lambda offline=False: llm)
    rep = pipeline.run("Zero Dep for cars up to 5 years, 15% of OD, 2 claims, mid-term allowed")
    assert rep.change.price_pct_of_od == 0.15 and rep.change.addon_code == "ZD"
    assert [c["step"] for c in rep.llm_calls] == ["understand", "understand (retry)", "explain"]
    assert rep.explanation == "**Summary.** ok"


def test_invalid_rule_falls_back_to_offline(monkeypatch):
    bad = {"kind": "modify_parameter", "rule_id": "NOPE", "param_path": ["x"], "new_value": 1}
    llm = make_llm([tool_reply(bad), tool_reply(bad), text_reply("x")])
    monkeypatch.setattr(pipeline, "LLM", lambda offline=False: llm)
    rep = pipeline.run("Raise the compulsory deductible for cars from 1,000 to 1,500 cc to Rs 1,500")
    assert rep.change.rule_id == "CLM-DEDUCT"          # offline stub took over
    assert any("bedrock failed" in c["backend"] for c in rep.llm_calls)


def test_addon_code_derived_when_missing(monkeypatch):
    data = {"kind": "new_addon", "addon_name": "Zero Depreciation", "max_car_age_years": 5,
            "price_pct_of_od": 0.15, "claim_limit_per_year": 2, "allow_midterm": True}
    llm = make_llm([tool_reply(data), text_reply("ok")])
    monkeypatch.setattr(pipeline, "LLM", lambda offline=False: llm)
    rep = pipeline.run("Zero Dep for cars up to 5 years, 15% of OD, 2 claims, mid-term allowed")
    assert rep.change.addon_code == "ZD" and any(i.rule_id == "ZD-ELIG" for i in rep.impact)
