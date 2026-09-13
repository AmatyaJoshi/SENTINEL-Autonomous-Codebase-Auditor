"""Pydantic output schemas for every LLM call. No free-text parsing anywhere else."""

from __future__ import annotations

from pydantic import BaseModel, Field

from sentinel.graph.state import Finding, HuntTarget


class PlanOutput(BaseModel):
    targets: list[HuntTarget] = Field(default_factory=list)


class HuntOutput(BaseModel):
    findings: list[Finding] = Field(default_factory=list)


class TestOutput(BaseModel):
    test_path: str
    test_code: str
    expected_failure: str = Field(description="Error type/message the test asserts on")


class PatchOutput(BaseModel):
    diff: str
    rationale: str


class ExplainOutput(BaseModel):
    summary: str
