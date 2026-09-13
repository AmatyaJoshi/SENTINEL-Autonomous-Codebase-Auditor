"""SQLModel tables: runs, findings, benchmark results."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlmodel import JSON, Column, Field, SQLModel


def _now() -> datetime:
    return datetime.now(UTC)


def _uuid() -> str:
    return uuid4().hex


class Run(SQLModel, table=True):
    __tablename__ = "runs"

    id: str = Field(default_factory=_uuid, primary_key=True)
    repo_url: str
    repo_path: str | None = None
    commit_sha: str | None = None
    language: str | None = None
    status: str = "created"
    started_at: datetime = Field(default_factory=_now)
    finished_at: datetime | None = None
    cost_usd: float = 0.0
    config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))


class FindingRecord(SQLModel, table=True):
    __tablename__ = "findings"

    id: str = Field(default_factory=_uuid, primary_key=True)
    run_id: str = Field(foreign_key="runs.id", index=True)
    category: str
    severity: str
    file: str
    line_start: int
    line_end: int
    symbol: str | None = None
    description: str
    hypothesis: str
    confidence: float
    status: str = "candidate"
    test_path: str | None = None
    test_code: str | None = None
    patch_diff: str | None = None
    verify_log: str | None = None
    regress_log: str | None = None
    pr_url: str | None = None
    cost_usd: float = 0.0
    created_at: datetime = Field(default_factory=_now)
    evidence: list[str] = Field(default_factory=list, sa_column=Column(JSON))


class BenchResult(SQLModel, table=True):
    __tablename__ = "bench_results"

    id: str = Field(default_factory=_uuid, primary_key=True)
    suite: str
    arm: str
    repo: str
    commit_sha: str
    mutation_id: str | None = None
    run_id: str | None = Field(default=None, foreign_key="runs.id")
    precision: float | None = None
    recall: float | None = None
    f1: float | None = None
    cost_usd: float = 0.0
    wall_clock_s: float = 0.0
    created_at: datetime = Field(default_factory=_now)
    metrics: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
