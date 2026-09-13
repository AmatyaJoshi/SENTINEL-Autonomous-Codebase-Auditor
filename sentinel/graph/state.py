"""LangGraph state schema (SPEC.md §3.1)."""

from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict

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
Severity = Literal["critical", "high", "medium", "low"]
Status = Literal[
    "candidate", "test_written", "verified", "refuted", "fixed", "regressed", "pr_opened"
]
Language = Literal["python", "typescript", "mixed"]


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
    status: Status = "candidate"
    test_path: str | None = None
    test_code: str | None = None
    patch_diff: str | None = None
    verify_log: str | None = None
    regress_log: str | None = None
    pr_url: str | None = None
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


class AuditState(TypedDict, total=False):
    run_id: str
    repo_url: str
    repo_path: str
    language: Language
    commit_sha: str
    index_ready: bool
    plan: list[HuntTarget]
    findings: Annotated[list[Finding], operator.add]
    budget: Budget
    spent: Budget
    messages: Annotated[list[BaseMessage], add_messages]
    errors: Annotated[list[str], operator.add]
