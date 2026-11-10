"""Data models shared by every part of ChangeGuard."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class Rule(BaseModel):
    """One business rule of the insurance product.

    Rules are linked through the data fields they read (inputs) and write
    (outputs): if rule A writes a field that rule B reads, A affects B.
    """

    id: str
    area: str
    name: str
    description: str
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    attributes: dict[str, Any] = Field(default_factory=dict)
    exercise: list[dict[str, Any]] = Field(default_factory=list, description="How tests can exercise this rule's limits")
    assumptions: list[dict[str, Any]] = Field(default_factory=list, description="What this rule takes for granted")
    tags: list[str] = Field(default_factory=list, description="Capabilities a new rule introduces")
    is_new: bool = False


class ChangeRequest(BaseModel):
    """A product change in structured form. The language model fills this in;
    everything after it is deterministic code."""

    kind: Literal["new_addon", "modify_parameter"]
    summary: str = Field(description="One-line restatement of the change")

    # new_addon
    addon_code: Optional[str] = Field(None, description="Short code, e.g. ZD")
    addon_name: Optional[str] = None
    max_car_age_years: Optional[float] = Field(None, description="Inclusive upper age limit")
    price_pct_of_od: Optional[float] = Field(None, description="Add-on price as a fraction of OD premium, e.g. 0.15")
    claim_limit_per_year: Optional[int] = None
    allow_midterm: bool = False
    excluded_causes: list[str] = Field(default_factory=lambda: ["theft", "total_loss"])

    # modify_parameter
    rule_id: Optional[str] = None
    param_path: Optional[list[str]] = Field(None, description="Path inside params, e.g. ['by_band', '1000_1500']")
    new_value: Optional[float] = None

    requirements: list[str] = Field(default_factory=list, description="Requirement clauses, one per line")


class ImpactItem(BaseModel):
    rule_id: str
    area: str
    name: str
    distance: int
    path: list[str]
    status: Literal["new", "changed", "affected"]


class Conflict(BaseModel):
    id: str
    severity: Literal["high", "medium"]
    rules: list[str]
    message: str
    fix: str


class TestCase(BaseModel):
    id: str
    title: str
    kind: Literal["quote", "midterm", "claim", "renewal"]
    technique: str
    requirement: str
    rules: list[str]
    inputs: dict[str, Any]
    expected: Any
    origin: Literal["generated", "regression"] = "generated"
    stale: bool = False
    old_expected: Any = None


class TestResult(BaseModel):
    test_id: str
    passed: bool
    expected: Any
    actual: Any
    note: str = ""
