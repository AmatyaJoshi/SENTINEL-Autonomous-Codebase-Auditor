"""Pydantic output schemas for every LLM call. No free-text parsing anywhere else."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from sentinel.graph.state import Category, Severity


class HuntTargetOut(BaseModel):
    file: str
    symbol: str | None = None
    reason: str
    risk_score: float = Field(ge=0.0, le=10.0)


class PlanOutput(BaseModel):
    targets: list[HuntTargetOut] = Field(default_factory=list)


class HuntFinding(BaseModel):
    """What the hunter emits. IDs, status and costs are assigned by the graph, not the model."""

    category: Category
    severity: Severity
    file: str
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    symbol: str | None = None
    description: str = Field(min_length=10)
    hypothesis: str = Field(min_length=10, description="Concrete triggering input")
    evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class HuntOutput(BaseModel):
    findings: list[HuntFinding] = Field(default_factory=list)
    notes: str = ""


class TestOutput(BaseModel):
    test_path: str
    test_code: str = Field(min_length=20)
    expected_failure: str = Field(description="Exception type or 'assertion: expected X got Y'")


class PatchOutput(BaseModel):
    diff: str = Field(min_length=10)
    rationale: str


class ExplainOutput(BaseModel):
    summary: str = Field(min_length=20)


class TriageOutput(BaseModel):
    """Zero-shot LLM judge baseline for the triage classifier (SPEC §7.4 eval arm c)."""

    will_verify: bool
    probability: float = Field(ge=0.0, le=1.0)
    reason: str


Tier = Literal["cheap", "strong"]
