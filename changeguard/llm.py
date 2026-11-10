"""The only module that talks to a language model.

Two jobs, nothing else:
  1. parse_change: plain-English change request -> ChangeRequest (structured)
  2. explain:      results -> a short report for a delivery analyst

Backend: Amazon Bedrock (Amazon Nova) through the Converse API. With no AWS
credentials, or CHANGEGUARD_OFFLINE=1, a rule-based stub is used so the whole
pipeline still runs. Every call is logged (tokens, latency) for cost transparency.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field

from .models import ChangeRequest

MODEL_ID = os.getenv("CHANGEGUARD_MODEL_ID", "amazon.nova-lite-v1:0")
REGION = os.getenv("AWS_REGION", "us-east-1")


@dataclass
class CallLog:
    step: str
    backend: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0


@dataclass
class LLM:
    offline: bool = False
    calls: list[CallLog] = field(default_factory=list)

    def __post_init__(self):
        self.offline = self.offline or os.getenv("CHANGEGUARD_OFFLINE") == "1"
        self._client = None
        if not self.offline:
            try:
                import boto3
                self._client = boto3.client("bedrock-runtime", region_name=REGION)
            except Exception:
                self.offline = True

    @property
    def backend(self) -> str:
        return "offline stub" if self.offline else f"Amazon Bedrock ({MODEL_ID})"

    # ---------- 1. understand the change ----------
    def parse_change(self, text: str, catalog_summary: str, validate=None) -> ChangeRequest:
        if not self.offline:
            try:
                return self._bedrock_parse(text, catalog_summary, validate)
            except Exception as exc:  # no credentials, throttling, bad output: fall back, never stop
                self.calls.append(CallLog("understand", f"bedrock failed: {type(exc).__name__}", MODEL_ID))
                self.offline = True
        if self.offline:
            t0 = time.time()
            cr = _offline_parse(text)
            self.calls.append(CallLog("understand", "offline", "-", seconds=round(time.time() - t0, 3)))
            return cr

    def _bedrock_parse(self, text: str, catalog_summary: str, validate=None) -> ChangeRequest:
        system = ("You turn an insurance product change request into a structured record by calling record_change. "
                  "Use only facts stated in the request; never invent numbers; leave unknown fields out. "
                  "Ages in years. Percentages as fractions (15% -> 0.15). "
                  "kind is 'new_addon' for a new optional cover, 'modify_parameter' for changing a number in an "
                  "existing rule. For modify_parameter pick rule_id and param_path from this catalog:\n" + catalog_summary)
        tool = {"toolSpec": {"name": "record_change", "description": "Record the structured change request.",
                             "inputSchema": {"json": TOOL_SCHEMA}}}
        messages = [{"role": "user", "content": [{"text": text}]}]
        last_error = None
        for attempt in range(2):  # one retry, telling the model what was wrong
            t0 = time.time()
            resp = self._client.converse(
                modelId=MODEL_ID, system=[{"text": system}], messages=messages,
                toolConfig={"tools": [tool], "toolChoice": {"any": {}}},
                inferenceConfig={"maxTokens": 1200, "temperature": 0})
            self._log("understand" if attempt == 0 else "understand (retry)", resp, t0)
            content = resp["output"]["message"]["content"]
            data = next((b["toolUse"]["input"] for b in content if "toolUse" in b), None)
            if data is None:
                raw = "".join(b.get("text", "") for b in content)
                data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
            try:
                cr = _normalise(ChangeRequest(**{"summary": text.strip(), **data}))
                if validate:
                    validate(cr)  # e.g. the rule and parameter must exist in the catalog
                return cr
            except Exception as exc:
                last_error = exc
                messages = messages + [resp["output"]["message"], {"role": "user", "content": [
                    {"text": f"That record was invalid: {exc}. Call record_change again with corrected fields."}]}]
                if any("toolUse" in b for b in content):
                    tu = next(b["toolUse"] for b in content if "toolUse" in b)
                    messages[-1]["content"] = [{"toolResult": {"toolUseId": tu["toolUseId"], "status": "error",
                                                               "content": [{"text": f"Invalid: {exc}. Fix and call again."}]}}]
        raise last_error

    # ---------- generic structured call (used by the knowledge-build extractor) ----------
    def call_tool(self, step: str, system: str, user_text: str, tool_name: str, schema: dict,
                  max_tokens: int = 2000) -> dict:
        """One Converse call that must return a tool-use record matching `schema`."""
        if self.offline:
            raise RuntimeError("call_tool needs Amazon Bedrock (offline mode is on)")
        tool = {"toolSpec": {"name": tool_name, "description": f"Record the {tool_name} result.",
                             "inputSchema": {"json": schema}}}
        t0 = time.time()
        resp = self._client.converse(
            modelId=MODEL_ID, system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": user_text}]}],
            toolConfig={"tools": [tool], "toolChoice": {"any": {}}},
            inferenceConfig={"maxTokens": max_tokens, "temperature": 0})
        self._log(step, resp, t0)
        for block in resp["output"]["message"]["content"]:
            if "toolUse" in block:
                return block["toolUse"]["input"]
        raw = "".join(b.get("text", "") for b in resp["output"]["message"]["content"])
        return json.loads(raw[raw.find("{"): raw.rfind("}") + 1])

    # ---------- 5. explain ----------
    def explain(self, facts: dict, feedback: str | None = None) -> str:
        if not self.offline:
            try:
                return self._bedrock_explain(facts, feedback)
            except Exception as exc:
                self.calls.append(CallLog("explain", f"bedrock failed: {type(exc).__name__}", MODEL_ID))
        return _offline_explain(facts)

    def _bedrock_explain(self, facts: dict, feedback: str | None = None) -> str:
        prompt = ("Write a short impact report (under 220 words) for a delivery analyst at Flarre.AI "
                  "(spell the company exactly 'Flarre'). "
                  "Sections: Summary, Must fix before release, Test results, Recommendation. "
                  "Use only these facts; cite rule, test and conflict IDs exactly as written; "
                  "do not state any number that is not in the facts; do not recommend release if any test "
                  "failed or a high-severity conflict is open.\n\n" + json.dumps(facts, indent=1))
        if feedback:
            prompt += "\n\nA reviewer rejected your previous draft. " + feedback
        t0 = time.time()
        resp = self._client.converse(
            modelId=MODEL_ID, messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 700, "temperature": 0.2})
        self._log("explain", resp, t0)
        return "".join(b.get("text", "") for b in resp["output"]["message"]["content"])

    def _log(self, step, resp, t0):
        u = resp.get("usage", {})
        self.calls.append(CallLog(step, "bedrock", MODEL_ID, u.get("inputTokens", 0), u.get("outputTokens", 0),
                                  round(time.time() - t0, 2)))


# A compact, hand-written schema: small models follow it more reliably than a generated one.
TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["new_addon", "modify_parameter"]},
        "summary": {"type": "string", "description": "One-line restatement of the change"},
        "addon_code": {"type": "string", "description": "Short uppercase code, e.g. ZD for Zero Depreciation"},
        "addon_name": {"type": "string"},
        "max_car_age_years": {"type": "number", "description": "Inclusive upper car age limit in years"},
        "price_pct_of_od": {"type": "number", "description": "Price as a fraction of own-damage premium, 15% -> 0.15"},
        "claim_limit_per_year": {"type": "integer"},
        "allow_midterm": {"type": "boolean", "description": "True if it can be added during the policy year"},
        "excluded_causes": {"type": "array", "items": {"type": "string"},
                            "description": "Claim causes it does not cover, e.g. theft, total_loss"},
        "rule_id": {"type": "string", "description": "For modify_parameter: rule id from the catalog"},
        "param_path": {"type": "array", "items": {"type": "string"},
                       "description": "For modify_parameter: keys inside params, e.g. [\"by_band\", \"1000_1500\"]"},
        "new_value": {"type": "number"},
        "requirements": {"type": "array", "items": {"type": "string"}, "description": "Requirement clauses, one each"},
    },
    "required": ["kind", "summary"],
}


def _normalise(cr: ChangeRequest) -> ChangeRequest:
    """Deterministic clean-up of common model slips, then a completeness check."""
    if cr.price_pct_of_od is not None and cr.price_pct_of_od > 1:
        cr.price_pct_of_od = cr.price_pct_of_od / 100
    if not cr.addon_code and cr.addon_name:  # model skipped the code: derive it, e.g. Zero Depreciation -> ZD
        cr.addon_code = "".join(w[0] for w in re.findall(r"[A-Za-z0-9]+", cr.addon_name))
    if cr.addon_code:
        cr.addon_code = re.sub(r"[^A-Z0-9]", "", cr.addon_code.upper())[:6] or "ADDON"
    cr.excluded_causes = [c.strip().lower().replace(" ", "_") for c in cr.excluded_causes] or ["theft", "total_loss"]
    if cr.kind == "new_addon":
        missing = [f for f in ("max_car_age_years", "price_pct_of_od", "claim_limit_per_year") if getattr(cr, f) is None]
        if missing:
            raise ValueError(f"missing fields for a new add-on: {', '.join(missing)}")
    else:
        if not (cr.rule_id and cr.param_path and cr.new_value is not None):
            raise ValueError("modify_parameter needs rule_id, param_path and new_value")
    return cr


# ---------------- offline stand-ins ----------------
def _num(pattern, text, cast=float):
    m = re.search(pattern, text, re.I)
    return cast(m.group(1).replace(",", "")) if m else None


def _offline_parse(text: str) -> ChangeRequest:
    t = text.lower()
    if "deductible" in t:
        new = _num(r"to\s*(?:rs\.?|₹|inr)?\s*([\d,]+)", text)
        band = "above_1500" if re.search(r"above\s*1,?500|over\s*1,?500", t) else "1000_1500"
        return ChangeRequest(kind="modify_parameter", summary=text.strip(), rule_id="CLM-DEDUCT",
                             param_path=["by_band", band], new_value=new)
    pct = _num(r"(\d+(?:\.\d+)?)\s*%", text)
    return ChangeRequest(
        kind="new_addon", summary=text.strip(),
        addon_code="ZD" if "zero dep" in t or "nil dep" in t else "ADDON",
        addon_name="Zero Depreciation" if "zero dep" in t or "nil dep" in t else "New add-on",
        max_car_age_years=_num(r"up to\s*(\d+(?:\.\d+)?)\s*years?", text) or 5,
        price_pct_of_od=(pct / 100) if pct else 0.15,
        claim_limit_per_year=_num(r"(\d+)\s*claims?", text, int) or 2,
        allow_midterm=bool(re.search(r"mid[- ]?(term|year)|during the (year|policy)", t)))


def _offline_explain(f: dict) -> str:
    lines = [f"**Summary.** Change: {f['change'].rstrip('.')}. Result: {f['impacted_rules']} rules affected across "
             f"{', '.join(f['areas'])}; {len(f['conflicts'])} conflicts found.", "", "**Must fix before release.**"]
    lines += [f"- {c['id']} ({c['severity']}): {c['message']}" for c in f["conflicts"] if c["severity"] == "high"] or ["- None."]
    lines += ["", f"**Test results.** {f['passed']} of {f['total']} tests passed."]
    lines += [f"- {x['id']}: {x['title']}. {x['note']}" for x in f["failures"]]
    if f.get("mutation_score") is not None:
        lines += ["", f"**Test strength.** The suite caught {f['mutation_score']:.0%} of {f['mutants']} random defects "
                      f"(a typical-case suite caught {f['baseline_mutation_score']:.0%})."]
    if f.get("stale"):
        lines += ["", f"**Stale regression tests.** {', '.join(f['stale'])} need new expected values."]
    lines += ["", "**Recommendation.** " + ("Do not release until the failures and high conflicts are fixed."
                                            if f["failures"] or any(c["severity"] == "high" for c in f["conflicts"])
                                            else "Ready for analyst sign-off.")]
    return "\n".join(lines)
