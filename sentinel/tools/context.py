"""Shared per-run context handed to tools and graph nodes."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sentinel.analyzers.base import AnalyzerFinding
from sentinel.config import Settings
from sentinel.graph.nodes.ingest import IngestResult
from sentinel.indexing.callgraph import CallGraph
from sentinel.indexing.embeddings import Embedder
from sentinel.indexing.store import IndexStore
from sentinel.llm.router import LLMRouter
from sentinel.sandbox.docker_runner import DockerRunner
from sentinel.sandbox.test_runner import Framework

EventSink = Callable[[str, dict[str, Any]], None]


@dataclass
class RunContext:
    run_id: str
    settings: Settings
    router: LLMRouter
    repo_path: Path
    run_dir: Path
    ingest: IngestResult | None = None
    repo_id: str | None = None
    store: IndexStore | None = None
    embedder: Embedder | None = None
    callgraph: CallGraph | None = None
    analyzer_findings: list[AnalyzerFinding] = field(default_factory=list)
    sandbox: DockerRunner | None = None
    sandbox_image: str | None = None
    workspace: Path | None = None
    baseline_failures: set[str] | None = None
    emit: EventSink = lambda _t, _d: None
    arm: str = "full"
    open_pr: bool = False
    review: bool = False
    cancel_event: threading.Event = field(default_factory=threading.Event)
    started_at: float = 0.0

    @property
    def language(self) -> str:
        return self.ingest.language if self.ingest else "python"

    @property
    def framework(self) -> Framework:
        if self.ingest and self.ingest.test_frameworks:
            fw = self.ingest.test_frameworks[0]
            return fw if fw != "unknown" else ("pytest" if self.language == "python" else "vitest")
        return "pytest" if self.language == "python" else "vitest"

    def sandbox_ready(self) -> bool:
        return (
            self.sandbox is not None
            and self.sandbox_image is not None
            and self.workspace is not None
        )

    def log(self, message: str, level: str = "info", **extra: Any) -> None:
        self.emit("log", {"level": level, "message": message, **extra})


def wrap_data(source: str, content: str) -> str:
    """Delimit untrusted repository content so agents can tell data from instructions."""
    return f'<sentinel-data source="{source}">\n{content}\n</sentinel-data>'
