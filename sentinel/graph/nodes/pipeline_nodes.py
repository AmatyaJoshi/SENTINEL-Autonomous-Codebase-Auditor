"""Deterministic nodes: ingest, index, analyze (SPEC §3.2)."""

from __future__ import annotations

import time
from pathlib import Path

from sentinel.analyzers.base import run_all
from sentinel.analyzers.registry import default_adapters
from sentinel.graph.nodes.ingest import ingest as ingest_repo
from sentinel.graph.state import AuditState, Budget
from sentinel.indexing.callgraph import CallGraph
from sentinel.indexing.embeddings import make_embedder
from sentinel.indexing.pipeline import index_repo
from sentinel.indexing.store import open_store
from sentinel.sandbox.docker_runner import SandboxUnavailableError, make_workspace
from sentinel.sandbox.test_runner import run_tests
from sentinel.tools.context import RunContext


def ingest_node(state: AuditState, ctx: RunContext) -> AuditState:
    ctx.started_at = ctx.started_at or time.time()
    res = ingest_repo(state["repo_url"], ctx.settings)
    ctx.ingest = res
    ctx.repo_path = Path(res.repo_path)
    out: AuditState = {
        "repo_path": res.repo_path,
        "language": res.language,
        "commit_sha": res.commit_sha or "",
        "test_framework": ctx.framework,
        "sandbox_ready": False,
        "spent": state.get("spent") or Budget(),
    }
    ctx.log(f"ingested {res.repo_path} language={res.language} tests={res.test_frameworks}")

    # Sandbox image build is the ONLY step where network is allowed (SPEC §5).
    if ctx.sandbox is not None and ctx.arm not in ("analyzers", "single_shot"):
        try:
            image = ctx.sandbox.build_repo_image(
                ctx.repo_path,
                res.language,
                res.lockfile_hash,
                list(res.package_managers),
                ctx.run_dir / "sandbox-build",
            )
            ctx.sandbox_image = image
            ctx.workspace = make_workspace(ctx.repo_path, ctx.run_dir / "workspace")
            out["sandbox_ready"] = True
            ctx.log(f"sandbox image ready: {image}")
        except SandboxUnavailableError as e:
            ctx.log(f"sandbox unavailable: {e}; findings cannot be verified", level="warning")
            return {**out, "errors": [f"sandbox unavailable: {e}"]}
        except Exception as e:  # noqa: BLE001 - image build failures must not abort the audit
            ctx.log(f"sandbox image build failed: {e}", level="warning")
            return {**out, "errors": [f"sandbox build failed: {type(e).__name__}: {e}"]}
    return out


def index_node(state: AuditState, ctx: RunContext) -> AuditState:
    embedder = make_embedder(ctx.settings)
    store = open_store(ctx.settings.database_url, ctx.settings.work_dir, embedder.dim or 256)
    res = index_repo(
        ctx.repo_path,
        ctx.settings,
        store=store,
        embedder=embedder,
        commit_sha=state.get("commit_sha"),
    )
    ctx.store, ctx.embedder, ctx.repo_id = store, embedder, res.repo_id
    ctx.callgraph = CallGraph.load(ctx.settings.work_dir / "index" / res.repo_id / "callgraph.json")
    ctx.log(
        f"indexed {res.files} files, {res.symbols} symbols, {res.chunks} chunks, {res.edges} call edges"
    )
    return {"index_ready": True}


def analyze_node(state: AuditState, ctx: RunContext) -> AuditState:
    adapters = default_adapters(include_semgrep=ctx.settings.analyzers_semgrep)
    results = run_all(ctx.repo_path, ctx.language, adapters)
    ctx.analyzer_findings = [f for r in results for f in r.findings]
    errors = [f"analyzer {r.tool}: {r.error}" for r in results if not r.ok]
    summary = ", ".join(
        f"{r.tool}={len(r.findings)}" if not r.skipped_reason else f"{r.tool}=skipped"
        for r in results
    )
    ctx.log(f"analyzers: {summary}")
    return {"errors": errors} if errors else {}


def baseline_failures(ctx: RunContext) -> set[str]:
    """Run the existing suite once on the clean workspace; failing tests run twice (flaky guard)."""
    if ctx.baseline_failures is not None:
        return ctx.baseline_failures
    if (
        not ctx.sandbox_ready()
        or ctx.sandbox is None
        or ctx.workspace is None
        or ctx.sandbox_image is None
    ):
        ctx.baseline_failures = set()
        return ctx.baseline_failures
    first = run_tests(
        ctx.sandbox,
        ctx.sandbox_image,
        ctx.workspace,
        ctx.framework,
        timeout_s=ctx.settings.sandbox.suite_timeout_s,
    )
    failing = first.report.failing_ids()
    ctx.emit(
        "sandbox.exec",
        {
            "node": "regress",
            "command": "baseline suite",
            "exit_code": first.exec.exit_code,
            "duration_s": round(first.exec.duration_s, 1),
            "timed_out": first.exec.timed_out,
        },
    )
    if failing:
        second = run_tests(
            ctx.sandbox,
            ctx.sandbox_image,
            ctx.workspace,
            ctx.framework,
            timeout_s=ctx.settings.sandbox.suite_timeout_s,
        )
        failing |= (
            second.report.failing_ids()
        )  # anything failing in either run is pre-existing/flaky
    ctx.baseline_failures = failing
    ctx.log(f"baseline suite: {first.report.total} tests, {len(failing)} pre-existing failures")
    return failing
