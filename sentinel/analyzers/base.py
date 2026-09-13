"""AnalyzerAdapter protocol, normalised (SARIF-like) findings, parallel runner, SARIF export."""

from __future__ import annotations

import shutil
import subprocess
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from sentinel import __version__
from sentinel.graph.state import Category
from sentinel.telemetry.otel import span

Level = Literal["error", "warning", "note"]
DEFAULT_TIMEOUT_S = 600


class AnalyzerFinding(BaseModel):
    tool: str
    rule_id: str
    message: str
    file: str  # repo-relative POSIX
    line_start: int
    line_end: int
    col_start: int | None = None
    level: Level = "warning"
    category_hint: Category | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    @property
    def location(self) -> str:
        return f"{self.file}:{self.line_start}"


class AnalyzerResult(BaseModel):
    tool: str
    ok: bool
    findings: list[AnalyzerFinding] = Field(default_factory=list)
    error: str | None = None
    duration_s: float = 0.0
    skipped_reason: str | None = None


@runtime_checkable
class AnalyzerAdapter(Protocol):
    name: str
    languages: frozenset[str]

    def available(self, repo_path: Path) -> str | None:
        """Return None if runnable, else a human reason it will be skipped."""
        ...

    def run(self, repo_path: Path) -> list[AnalyzerFinding]: ...


def rel_posix(repo_path: Path, file: str) -> str:
    p = Path(file)
    try:
        if p.is_absolute():
            p = p.resolve().relative_to(repo_path.resolve())
    except ValueError:
        pass
    return p.as_posix().removeprefix("./")


def run_cmd(
    args: Sequence[str], cwd: Path, timeout: int = DEFAULT_TIMEOUT_S
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        list(args),
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def which(name: str) -> str | None:
    return shutil.which(name)


def run_adapter(adapter: AnalyzerAdapter, repo_path: Path) -> AnalyzerResult:
    t0 = time.perf_counter()
    with span("sentinel.analyze", tool=adapter.name) as s:
        reason = adapter.available(repo_path)
        if reason:
            s.set_attribute("sentinel.outcome", "skipped")
            return AnalyzerResult(tool=adapter.name, ok=True, skipped_reason=reason)
        try:
            findings = adapter.run(repo_path)
            s.set_attribute("sentinel.outcome", "ok")
            s.set_attribute("sentinel.findings", len(findings))
            return AnalyzerResult(
                tool=adapter.name,
                ok=True,
                findings=findings,
                duration_s=time.perf_counter() - t0,
            )
        except subprocess.TimeoutExpired:
            s.set_attribute("sentinel.outcome", "timeout")
            return AnalyzerResult(
                tool=adapter.name,
                ok=False,
                error="timeout",
                duration_s=time.perf_counter() - t0,
            )
        except Exception as e:  # noqa: BLE001 - one analyzer must not abort the run
            s.set_attribute("sentinel.outcome", "error")
            return AnalyzerResult(
                tool=adapter.name,
                ok=False,
                error=f"{type(e).__name__}: {e}",
                duration_s=time.perf_counter() - t0,
            )


def run_all(
    repo_path: Path,
    language: str,
    adapters: Sequence[AnalyzerAdapter],
    parallel: bool = True,
) -> list[AnalyzerResult]:
    wanted = [a for a in adapters if language in a.languages or language == "mixed"]
    if not parallel:
        return [run_adapter(a, repo_path) for a in wanted]
    with ThreadPoolExecutor(max_workers=max(1, len(wanted))) as ex:
        return list(ex.map(lambda a: run_adapter(a, repo_path), wanted))


def to_sarif(results: Sequence[AnalyzerResult]) -> dict[str, Any]:
    runs = []
    for r in results:
        rules: dict[str, dict[str, Any]] = {}
        sarif_results = []
        for f in r.findings:
            rules.setdefault(f.rule_id, {"id": f.rule_id, "properties": {}})
            if f.category_hint:
                rules[f.rule_id]["properties"]["sentinelCategory"] = f.category_hint
            region: dict[str, Any] = {"startLine": f.line_start, "endLine": f.line_end}
            if f.col_start is not None:
                region["startColumn"] = f.col_start
            sarif_results.append(
                {
                    "ruleId": f.rule_id,
                    "level": f.level,
                    "message": {"text": f.message},
                    "locations": [
                        {
                            "physicalLocation": {
                                "artifactLocation": {"uri": f.file},
                                "region": region,
                            }
                        }
                    ],
                    "properties": {"sentinelCategory": f.category_hint},
                }
            )
        runs.append(
            {
                "tool": {
                    "driver": {
                        "name": r.tool,
                        "rules": list(rules.values()),
                        "properties": {
                            "sentinelVersion": __version__,
                            "ok": r.ok,
                            "error": r.error,
                            "skippedReason": r.skipped_reason,
                            "durationSeconds": round(r.duration_s, 3),
                        },
                    }
                },
                "results": sarif_results,
            }
        )
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": runs,
    }
