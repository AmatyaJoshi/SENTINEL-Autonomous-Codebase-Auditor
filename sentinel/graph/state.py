"""LangGraph state schema (SPEC.md §3.1) plus the run-level bookkeeping the API exposes."""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

Category = Literal[
    "null_deref",
    "off_by_one",
    "unhandled_exception",
    "resource_leak",
    "race_condition",
    "type_error",
    "logic_error",
    "security_smell",
    "api_misuse",
    "dead_code",
    "perf",
]
CATEGORIES: tuple[str, ...] = (
    "null_deref",
    "off_by_one",
    "unhandled_exception",
    "resource_leak",
    "race_condition",
    "type_error",
    "logic_error",
    "security_smell",
    "api_misuse",
    "dead_code",
    "perf",
)
Severity = Literal["critical", "high", "medium", "low"]
SEVERITY_WEIGHT = {"critical": 4, "high": 3, "medium": 2, "low": 1}
Status = Literal[
    "candidate", "test_written", "verified", "refuted", "fixed", "regressed", "pr_opened"
]
Language = Literal["python", "typescript", "mixed"]
NODE_ORDER: tuple[str, ...] = (
    "ingest",
    "index",
    "analyze",
    "plan",
    "hunt",
    "triage",
    "verify",
    "fix",
    "regress",
    "rank",
    "report",
)


class Finding(BaseModel):
    id: str
    category: Category
    severity: Severity
    file: str
    line_start: int
    line_end: int
    symbol: str | None = None
    description: str
    evidence: list[str] = Field(default_factory=list)
    hypothesis: str
    confidence: float = Field(ge=0.0, le=1.0)
    hunter_confidence: float | None = None
    triage_score: float | None = None
    status: Status = "candidate"
    test_path: str | None = None
    test_code: str | None = None
    expected_failure: str | None = None
    patch_diff: str | None = None
    verify_log: str | None = None
    regress_log: str | None = None
    pr_url: str | None = None
    explanation: str | None = None
    blast_radius: int = 0
    rank: int | None = None
    review_decision: Literal["approve", "reject"] | None = None
    cost_usd: float = 0.0


class HuntTarget(BaseModel):
    file: str
    symbol: str | None = None
    reason: str
    risk_score: float = Field(ge=0.0)
    analyzer_hits: int = 0


class Budget(BaseModel):
    max_usd: float = 0.0
    max_minutes: float = 0.0
    max_findings: int = 0

    def exceeded_by(self, spent: Budget) -> bool:
        return (
            spent.max_usd > self.max_usd
            or spent.max_minutes > self.max_minutes
            or spent.max_findings > self.max_findings
        )


def _merge_findings(left: list[Finding], right: list[Finding]) -> list[Finding]:
    """Reducer: append new findings, replace existing ones by id (later nodes update status)."""
    by_id = {f.id: f for f in left}
    order = [f.id for f in left]
    for f in right:
        if f.id not in by_id:
            order.append(f.id)
        by_id[f.id] = f
    return [by_id[i] for i in order]


def _last(left: Any, right: Any) -> Any:
    return right if right is not None else left


class AuditState(TypedDict, total=False):
    run_id: str
    repo_url: str
    repo_path: str
    language: Language
    commit_sha: str
    index_ready: bool
    test_framework: str
    sandbox_ready: bool
    plan: list[HuntTarget]
    findings: Annotated[list[Finding], _merge_findings]
    budget: Budget
    spent: Budget
    messages: Annotated[list[BaseMessage], add_messages]
    errors: Annotated[list[str], operator.add]
    hunted_targets: Annotated[int, operator.add]
    stopped_early: str | None
