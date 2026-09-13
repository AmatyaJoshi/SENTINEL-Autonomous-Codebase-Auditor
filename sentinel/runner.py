"""Run orchestration: builds a RunContext + graph, persists runs/findings/events, supports cancel,
human review (interrupt before report), and replay from a checkpoint. Used by the CLI and the API.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import Engine
from sqlmodel import col, select

from sentinel.config import Settings
from sentinel.db.models import FindingRecord, Run, RunEvent
from sentinel.db.session import init_db, session_scope
from sentinel.graph.build import build_graph, initial_state, open_checkpointer
from sentinel.graph.state import NODE_ORDER, AuditState, Budget, Finding
from sentinel.llm.router import Backend, CallStats, LLMRouter
from sentinel.sandbox.docker_runner import DockerRunner
from sentinel.telemetry.otel import span
from sentinel.tools.context import RunContext

log = logging.getLogger("sentinel.runner")
Subscriber = Callable[[int, str, dict[str, Any]], None]


class EventBus:
    """Per-run sequence-numbered events: persisted to DB and fanned out to live subscribers."""

    def __init__(self, engine: Engine, run_id: str) -> None:
        self.engine = engine
        self.run_id = run_id
        self.seq = 0
        self._subs: list[Subscriber] = []
        self._lock = threading.Lock()

    def subscribe(self, fn: Subscriber) -> Callable[[], None]:
        with self._lock:
            self._subs.append(fn)

        def unsub() -> None:
            with self._lock:
                if fn in self._subs:
                    self._subs.remove(fn)

        return unsub

    def emit(self, type_: str, data: dict[str, Any]) -> None:
        with self._lock:
            self.seq += 1
            seq = self.seq
            subs = list(self._subs)
        try:
            with session_scope(self.engine) as s:
                s.add(RunEvent(run_id=self.run_id, seq=seq, type=type_, data=_jsonable(data)))
        except Exception as e:  # noqa: BLE001 - event persistence must never break a run
            log.warning("event persist failed: %s", e)
        for fn in subs:
            with contextlib.suppress(Exception):
                fn(seq, type_, data)


def _jsonable(d: dict[str, Any]) -> dict[str, Any]:
    import json

    out: dict[str, Any] = json.loads(json.dumps(d, default=str))
    return out


class RunManager:
    """Owns live runs (threads), their event buses and cancel flags. One per process."""

    def __init__(
        self,
        settings: Settings,
        engine: Engine,
        backend: Backend | None = None,
        sandbox_factory: Callable[[], DockerRunner | None] | None = None,
    ) -> None:
        self.settings = settings
        self.engine = engine
        self.backend = backend
        self.sandbox_factory = sandbox_factory or self._default_sandbox
        self.buses: dict[str, EventBus] = {}
        self.contexts: dict[str, RunContext] = {}
        self.graphs: dict[str, Any] = {}
        self.threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()
        init_db(engine)

    def _default_sandbox(self) -> DockerRunner | None:
        runner = DockerRunner(self.settings.sandbox)
        return runner if runner.available() else None

    # ------------------------------------------------------------------ lifecycle
    def active_count(self) -> int:
        return sum(1 for t in self.threads.values() if t.is_alive())

    def create(
        self,
        repo: str,
        *,
        budget: Budget | None = None,
        arm: str = "full",
        open_pr: bool = False,
        review: bool = False,
        created_by: str | None = None,
        sha: str | None = None,
    ) -> Run:
        run = Run(
            repo_url=repo,
            arm=arm,
            budget=(budget or self._default_budget()).model_dump(),
            created_by=created_by,
            nodes=[{"name": n, "status": "pending"} for n in NODE_ORDER],
            config={"open_pr": open_pr, "review": review, "sha": sha},
        )
        with session_scope(self.engine) as s:
            s.add(run)
            s.flush()
            s.refresh(run)
            s.expunge(run)
        return run

    def _default_budget(self) -> Budget:
        b = self.settings.budget
        return Budget(max_usd=b.max_usd, max_minutes=b.max_minutes, max_findings=b.max_findings)

    def start(self, run: Run, *, block: bool = False) -> None:
        if self.active_count() >= self.settings.max_concurrent_runs and not block:
            raise RuntimeError("max concurrent runs reached")
        bus = EventBus(self.engine, run.id)
        run_dir = self.settings.work_dir / "runs" / run.id
        run_dir.mkdir(parents=True, exist_ok=True)
        router = LLMRouter(
            self.settings, backend=self.backend, on_call=lambda st: self._on_llm(bus, st)
        )
        ctx = RunContext(
            run_id=run.id,
            settings=self.settings,
            router=router,
            repo_path=Path(run.repo_url),
            run_dir=run_dir,
            emit=bus.emit,
            arm=run.arm,
            open_pr=bool(run.config.get("open_pr")),
            review=bool(run.config.get("review")),
            sandbox=self.sandbox_factory(),
            started_at=time.time(),
        )
        graph = build_graph(
            ctx,
            checkpointer=open_checkpointer(self.settings.work_dir),
            interrupt_before_report=ctx.review,
        )
        with self._lock:
            self.buses[run.id], self.contexts[run.id], self.graphs[run.id] = bus, ctx, graph
        bus.subscribe(lambda seq, t, d: self._on_event(run.id, t, d))
        thread = threading.Thread(
            target=self._execute,
            args=(run, ctx, graph, None),
            name=f"run-{run.id[:8]}",
            daemon=True,
        )
        self.threads[run.id] = thread
        thread.start()
        if block:
            thread.join()

    def resume(self, run_id: str, *, from_node: str | None = None, block: bool = False) -> Run:
        """Replay: continue a checkpointed run, optionally rewinding to before `from_node`."""
        run = self.get(run_id)
        if run is None:
            raise KeyError(run_id)
        if run_id not in self.graphs:
            bus = EventBus(self.engine, run.id)
            with session_scope(self.engine) as s:
                last = s.exec(
                    select(RunEvent.seq)
                    .where(RunEvent.run_id == run_id)
                    .order_by(col(RunEvent.seq).desc())
                ).first()
            bus.seq = int(last or 0)
            router = LLMRouter(
                self.settings, backend=self.backend, on_call=lambda st: self._on_llm(bus, st)
            )
            ctx = RunContext(
                run_id=run.id,
                settings=self.settings,
                router=router,
                repo_path=Path(run.repo_path or run.repo_url),
                run_dir=self.settings.work_dir / "runs" / run.id,
                emit=bus.emit,
                arm=run.arm,
                open_pr=bool(run.config.get("open_pr")),
                review=bool(run.config.get("review")),
                sandbox=self.sandbox_factory(),
                started_at=time.time(),
            )
            graph = build_graph(
                ctx,
                checkpointer=open_checkpointer(self.settings.work_dir),
                interrupt_before_report=ctx.review,
            )
            with self._lock:
                self.buses[run.id], self.contexts[run.id], self.graphs[run.id] = bus, ctx, graph
            bus.subscribe(lambda seq, t, d: self._on_event(run.id, t, d))
        ctx, graph = self.contexts[run_id], self.graphs[run_id]
        ctx.cancel_event.clear()
        config: dict[str, Any] = {"configurable": {"thread_id": run_id}}
        if from_node:
            target = None
            for snap in graph.get_state_history(config):
                if from_node in (snap.next or ()):
                    target = snap
            if target is None:
                raise ValueError(f"no checkpoint found before node {from_node!r}")
            config = target.config
            # nodes before `from_node` are re-hydrated by re-running the deterministic prefix if needed
            self._rehydrate(ctx, graph, config)
        self._set_status(run_id, "running", error=None)
        thread = threading.Thread(
            target=self._execute,
            args=(run, ctx, graph, config),
            name=f"resume-{run_id[:8]}",
            daemon=True,
        )
        self.threads[run_id] = thread
        thread.start()
        if block:
            thread.join()
        return self.get(run_id) or run

    def _rehydrate(self, ctx: RunContext, graph: Any, config: dict[str, Any]) -> None:
        """Context objects (index store, call graph, analyzer hits, sandbox) are not in the checkpoint;
        rebuild them deterministically from the persisted state."""
        from sentinel.graph.nodes.pipeline_nodes import analyze_node, index_node, ingest_node

        state = graph.get_state(config).values
        if not state.get("repo_path"):
            return
        ingest_node(state, ctx)
        if state.get("index_ready"):
            index_node(state, ctx)
        analyze_node(state, ctx)

    def cancel(self, run_id: str) -> Run | None:
        ctx = self.contexts.get(run_id)
        if ctx is not None:
            ctx.cancel_event.set()
            ctx.log("cancellation requested", level="warning")
        return (
            self._set_status(run_id, "cancelled")
            if ctx is None or not self.threads.get(run_id, threading.Thread()).is_alive()
            else self.get(run_id)
        )

    def decide(self, run_id: str, finding_id: str, decision: str, note: str = "") -> Finding | None:
        graph = self.graphs.get(run_id)
        if graph is None:
            raise KeyError("run is not resumable in this process")
        config = {"configurable": {"thread_id": run_id}}
        state = graph.get_state(config).values
        target = next((f for f in state.get("findings", []) if f.id == finding_id), None)
        if target is None:
            return None
        updated = target.model_copy(
            update={
                "review_decision": decision,
                "evidence": [*target.evidence, f"review: {decision} {note}".strip()],
            }
        )
        graph.update_state(config, {"findings": [updated]}, as_node="rank")
        self._persist_findings(run_id, [updated])
        self.buses[run_id].emit("finding.update", updated.model_dump())
        pending = [
            f
            for f in state.get("findings", [])
            if f.status == "fixed" and f.review_decision is None and f.id != finding_id
        ]
        if not pending:
            self.resume(run_id)
        return cast(Finding, updated)

    # ------------------------------------------------------------------ execution
    def _execute(
        self, run: Run, ctx: RunContext, graph: Any, config: dict[str, Any] | None
    ) -> None:
        cfg = config or {"configurable": {"thread_id": run.id}}
        cfg.setdefault("configurable", {})["thread_id"] = run.id
        cfg["max_concurrency"] = self.settings.hunt_concurrency
        self._set_status(run.id, "running")
        ctx.emit("run.status", {"status": "running", "current_node": "ingest", "progress": 0.0})
        budget = Budget.model_validate(run.budget) if run.budget else self._default_budget()
        inp: AuditState | None = None if config else initial_state(run.id, run.repo_url, budget)
        final: dict[str, Any] = {}
        try:
            with span("sentinel.audit", run_id=run.id, repo=run.repo_url):
                if inp is not None and config is None:
                    # resume of an interrupted (review) run passes None as input
                    existing = (
                        graph.get_state(cfg).values if run.status in ("awaiting_review",) else None
                    )
                    if existing:
                        inp = None
                final = graph.invoke(inp, cfg) or {}
            snapshot = graph.get_state(cfg)
            if snapshot.next and "report" in snapshot.next:
                self._persist_findings(run.id, snapshot.values.get("findings", []))
                self._set_status(run.id, "awaiting_review", current_node="report")
                ctx.emit(
                    "run.status",
                    {"status": "awaiting_review", "current_node": "report", "progress": 0.95},
                )
                return
            findings = final.get("findings", snapshot.values.get("findings", []))
            self._persist_findings(run.id, findings)
            status = "cancelled" if ctx.cancel_event.is_set() else "completed"
            self._set_status(
                run.id,
                status,
                current_node="report",
                cost=ctx.router.total_cost_usd,
                commit=final.get("commit_sha"),
                language=final.get("language"),
                repo_path=final.get("repo_path"),
            )
        except Exception as e:  # noqa: BLE001 - never fail silently
            log.exception("run %s failed", run.id)
            self._set_status(
                run.id, "failed", error=f"{type(e).__name__}: {e}", cost=ctx.router.total_cost_usd
            )
            ctx.emit("log", {"level": "error", "message": f"run failed: {type(e).__name__}: {e}"})
        finally:
            r = self.get(run.id)
            if r is not None:
                ctx.emit("run.end", run_to_dict(r))
            if ctx.store is not None:
                with contextlib.suppress(Exception):
                    ctx.store.close()

    # ------------------------------------------------------------------ persistence
    def _on_llm(self, bus: EventBus, st: CallStats) -> None:
        bus.emit(
            "llm.call",
            {
                "node": st.prompt_name,
                "model": st.model,
                "tokens_in": st.tokens_in,
                "tokens_out": st.tokens_out,
                "cost_usd": round(st.cost_usd, 5),
                "duration_s": round(st.duration_s, 2),
                "cached": st.cached,
                "prompt_version": st.prompt_version,
            },
        )

    def _on_event(self, run_id: str, type_: str, data: dict[str, Any]) -> None:
        if type_ not in ("node.start", "node.end", "finding.new", "finding.update", "llm.call"):
            return
        with session_scope(self.engine) as s:
            run = s.get(Run, run_id)
            if run is None:
                return
            nodes = [
                dict(n) for n in (run.nodes or [])
            ]  # new objects so SQLAlchemy sees the change
            if type_ in ("node.start", "node.end"):
                name = data["node"]
                for n in nodes:
                    if n["name"] == name:
                        if type_ == "node.start":
                            n.update(
                                status="running",
                                started_at=datetime.fromtimestamp(data["at"], tz=UTC).isoformat(),
                            )
                        else:
                            n.update(
                                status="done" if data.get("ok", True) else "error",
                                finished_at=datetime.fromtimestamp(data["at"], tz=UTC).isoformat(),
                                duration_s=data.get("duration_s"),
                            )
                run.nodes = nodes
                done = sum(1 for n in nodes if n["status"] in ("done", "error", "skipped"))
                run.progress = round(done / len(NODE_ORDER), 3)
                run.current_node = name
            if type_ in ("finding.new", "finding.update"):
                counts = dict(run.counts or {})
                if type_ == "finding.new":
                    counts["candidate"] = counts.get("candidate", 0) + 1
                run.counts = counts
            if type_ == "llm.call":
                run.cost_usd = round(run.cost_usd + float(data.get("cost_usd", 0.0)), 5)
            s.add(run)

    def _persist_findings(self, run_id: str, findings: list[Finding]) -> None:
        counts: dict[str, int] = {}
        with session_scope(self.engine) as s:
            for f in findings:
                counts[f.status] = counts.get(f.status, 0) + 1
                rec = s.get(FindingRecord, f.id)
                data = f.model_dump()
                if rec is None:
                    s.add(FindingRecord(run_id=run_id, **data))
                else:
                    for k, v in data.items():
                        setattr(rec, k, v)
                    s.add(rec)
            run = s.get(Run, run_id)
            if run is not None:
                run.counts = counts
                s.add(run)

    def _set_status(
        self,
        run_id: str,
        status: str,
        *,
        current_node: str | None = None,
        error: str | None = None,
        cost: float | None = None,
        commit: str | None = None,
        language: str | None = None,
        repo_path: str | None = None,
    ) -> Run | None:
        with session_scope(self.engine) as s:
            run = s.get(Run, run_id)
            if run is None:
                return None
            run.status = status
            if current_node:
                run.current_node = current_node
            if error is not None or status in ("running",):
                run.error = error
            if cost is not None:
                run.cost_usd = round(cost, 5)
            if commit:
                run.commit_sha = commit
            if language:
                run.language = language
            if repo_path:
                run.repo_path = repo_path
            if status in ("completed", "failed", "cancelled"):
                run.finished_at = datetime.now(UTC)
                run.progress = 1.0
                run.nodes = [
                    {**n, "status": "skipped"} if n["status"] == "pending" else dict(n)
                    for n in (run.nodes or [])
                ]
            s.add(run)
            s.flush()
            s.refresh(run)
            s.expunge(run)
            return run

    # ------------------------------------------------------------------ queries
    def get(self, run_id: str) -> Run | None:
        with session_scope(self.engine) as s:
            run = s.get(Run, run_id)
            if run is not None:
                s.expunge(run)
            return run

    def list_runs(
        self, limit: int = 50, offset: int = 0, status: str | None = None
    ) -> tuple[list[Run], int]:
        with session_scope(self.engine) as s:
            q = select(Run)
            if status:
                q = q.where(Run.status == status)
            rows = s.exec(q.order_by(col(Run.started_at).desc()).offset(offset).limit(limit)).all()
            total = len(
                s.exec(
                    select(Run.id).where(Run.status == status) if status else select(Run.id)
                ).all()
            )
            for r in rows:
                s.expunge(r)
            return list(rows), total

    def findings(self, run_id: str) -> list[FindingRecord]:
        with session_scope(self.engine) as s:
            rows = s.exec(
                select(FindingRecord)
                .where(FindingRecord.run_id == run_id)
                .order_by(col(FindingRecord.rank))
            ).all()
            for r in rows:
                s.expunge(r)
            return list(rows)

    def events(self, run_id: str, after_seq: int = 0, limit: int = 2000) -> list[RunEvent]:
        with session_scope(self.engine) as s:
            rows = s.exec(
                select(RunEvent)
                .where(RunEvent.run_id == run_id, RunEvent.seq > after_seq)
                .order_by(col(RunEvent.seq))
                .limit(limit)
            ).all()
            for r in rows:
                s.expunge(r)
            return list(rows)

    def delete(self, run_id: str) -> bool:
        with session_scope(self.engine) as s:
            run = s.get(Run, run_id)
            if run is None:
                return False
            for f in s.exec(select(FindingRecord).where(FindingRecord.run_id == run_id)).all():
                s.delete(f)
            for e in s.exec(select(RunEvent).where(RunEvent.run_id == run_id)).all():
                s.delete(e)
            s.delete(run)
        return True


def run_to_dict(run: Run) -> dict[str, Any]:
    return {
        "id": run.id,
        "repo_url": run.repo_url,
        "repo_path": run.repo_path,
        "commit_sha": run.commit_sha,
        "language": run.language,
        "arm": run.arm,
        "status": run.status,
        "current_node": run.current_node,
        "progress": run.progress,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "cost_usd": run.cost_usd,
        "error": run.error,
        "budget": run.budget,
        "counts": run.counts,
        "nodes": run.nodes,
        "created_by": run.created_by,
    }


def new_run_id() -> str:
    return uuid4().hex
