"""StateGraph assembly (SPEC §3.3): edges, Send fan-out, budget guard, checkpointer, interrupt."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from functools import wraps
from pathlib import Path
from typing import Any, cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send

from sentinel.graph.nodes.fix import fix_node, regress_node
from sentinel.graph.nodes.hunt import hunt_one
from sentinel.graph.nodes.pipeline_nodes import analyze_node, index_node, ingest_node
from sentinel.graph.nodes.plan import plan_node
from sentinel.graph.nodes.rank import rank_node
from sentinel.graph.nodes.report import report_node
from sentinel.graph.nodes.triage import triage_node
from sentinel.graph.nodes.verify import verify_node
from sentinel.graph.state import NODE_ORDER, AuditState, Budget
from sentinel.tools.context import RunContext

LLM_NODES = ("plan", "hunt", "verify", "fix", "rank")


def _spent(ctx: RunContext, state: AuditState) -> Budget:
    return Budget(
        max_usd=round(ctx.router.total_cost_usd, 4),
        max_minutes=round((time.time() - ctx.started_at) / 60.0, 2) if ctx.started_at else 0.0,
        max_findings=sum(1 for f in state.get("findings", []) if f.status not in ("refuted",)),
    )


def check_budget(ctx: RunContext, next_node: str) -> Callable[[AuditState], str]:
    """Conditional edge before every LLM node. Over budget or cancelled → rank with partial results."""

    def route(state: AuditState) -> str:
        if ctx.cancel_event.is_set():
            return "rank"
        budget = state.get("budget") or Budget()
        spent = _spent(ctx, state)
        if budget.max_usd and spent.max_usd > budget.max_usd:
            ctx.log(f"budget exceeded: ${spent.max_usd:.2f} > ${budget.max_usd}", level="warning")
            return "rank"
        if budget.max_minutes and spent.max_minutes > budget.max_minutes:
            ctx.log(f"time budget exceeded: {spent.max_minutes:.1f} min", level="warning")
            return "rank"
        if (
            budget.max_findings
            and next_node in ("verify", "fix")
            and spent.max_findings > budget.max_findings
        ):
            return "rank"
        return next_node

    return route


def _wrap(
    name: str, fn: Callable[[Any, RunContext], AuditState], ctx: RunContext
) -> Callable[..., Any]:
    @wraps(fn)
    def node(state: Any) -> Any:
        from sentinel.telemetry.otel import span

        t0 = time.time()
        ctx.emit("node.start", {"node": name, "at": t0})
        with span(f"sentinel.node.{name}", run_id=ctx.run_id, node=name) as s:
            try:
                out = fn(state, ctx) or {}
                ok = True
            except Exception as e:  # noqa: BLE001 - recorded, never silent (SPEC §3.3)
                ok = False
                s.set_attribute("sentinel.outcome", "error")
                ctx.log(f"{name} failed: {type(e).__name__}: {e}", level="error")
                out = {"errors": [f"{name}: {type(e).__name__}: {e}"]}
            spent = _spent(ctx, cast(AuditState, state if isinstance(state, dict) else {}))
            if name != "hunt":  # fan-out branches must not write shared scalars concurrently
                out = {**out, "spent": spent}
        ctx.emit(
            "node.end",
            {"node": name, "at": time.time(), "duration_s": round(time.time() - t0, 2), "ok": ok},
        )
        return out

    return node


def fan_out_hunt(state: AuditState) -> list[Send]:
    return [
        Send("hunt", {"target": t.model_dump(), "run_id": state.get("run_id")})
        for t in state.get("plan", [])
    ]


def build_graph(
    ctx: RunContext,
    checkpointer: BaseCheckpointSaver | None = None,  # type: ignore[type-arg]
    interrupt_before_report: bool = False,
) -> CompiledStateGraph:  # type: ignore[type-arg]
    g: StateGraph[Any] = StateGraph(AuditState)
    g.add_node("ingest", _wrap("ingest", ingest_node, ctx))
    g.add_node("index", _wrap("index", index_node, ctx))
    g.add_node("analyze", _wrap("analyze", analyze_node, ctx))
    g.add_node("plan", _wrap("plan", plan_node, ctx))
    g.add_node("hunt", _wrap("hunt", hunt_one, ctx))
    g.add_node("triage", _wrap("triage", triage_node, ctx))
    g.add_node("verify", _wrap("verify", verify_node, ctx))
    g.add_node("fix", _wrap("fix", fix_node, ctx))
    g.add_node("regress", _wrap("regress", regress_node, ctx))
    g.add_node("rank", _wrap("rank", rank_node, ctx))
    g.add_node("report", _wrap("report", report_node, ctx))

    g.add_edge(START, "ingest")
    g.add_edge("ingest", "index")
    g.add_edge("index", "analyze")
    g.add_conditional_edges("analyze", check_budget(ctx, "plan"), ["plan", "rank"])

    def after_plan(state: AuditState) -> list[Send] | str:
        route = check_budget(ctx, "hunt")(state)
        if route != "hunt" or not state.get("plan"):
            return "rank"
        return fan_out_hunt(state)

    g.add_conditional_edges("plan", after_plan, ["hunt", "rank"])
    g.add_edge("hunt", "triage")
    g.add_conditional_edges("triage", check_budget(ctx, "verify"), ["verify", "rank"])
    g.add_conditional_edges("verify", check_budget(ctx, "fix"), ["fix", "rank"])
    g.add_edge("fix", "regress")
    g.add_edge("regress", "rank")
    g.add_edge("rank", "report")
    g.add_edge("report", END)

    return g.compile(
        checkpointer=checkpointer,
        interrupt_before=["report"] if interrupt_before_report else None,
    )


def open_checkpointer(work_dir: Path, database_url: str | None = None) -> BaseCheckpointSaver:  # type: ignore[type-arg]
    """SqliteSaver for dev; PostgresSaver (langgraph-checkpoint-postgres) when DATABASE_URL is Postgres,
    so several API/worker replicas share checkpoints and `replay` works from any of them."""
    if database_url and database_url.startswith("postgresql"):
        try:
            from langgraph.checkpoint.postgres import PostgresSaver
            from psycopg import Connection
            from psycopg.rows import dict_row

            conn = Connection.connect(
                database_url.replace("postgresql+psycopg://", "postgresql://"),
                autocommit=True,
                prepare_threshold=0,
                row_factory=dict_row,
            )
            saver = PostgresSaver(conn, serde=_serde())
            saver.setup()
            return cast(BaseCheckpointSaver, saver)  # type: ignore[type-arg]
        except ImportError as e:  # pragma: no cover - needs the postgres extra
            raise RuntimeError("install the `postgres` extra for PostgresSaver") from e
    work_dir.mkdir(parents=True, exist_ok=True)
    conn_s = sqlite3.connect(work_dir / "checkpoints.sqlite", check_same_thread=False)
    return SqliteSaver(conn_s, serde=_serde())


def _serde() -> Any:
    """Allow our Pydantic state models through the msgpack serializer (LangGraph strict mode)."""
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    allowed = [("sentinel.graph.state", n) for n in ("Finding", "HuntTarget", "Budget")]
    try:
        return JsonPlusSerializer(allowed_msgpack_modules=allowed)
    except TypeError:  # older langgraph without the parameter
        return JsonPlusSerializer()


def memory_checkpointer() -> BaseCheckpointSaver:  # type: ignore[type-arg]
    return MemorySaver()


def initial_state(run_id: str, repo: str, budget: Budget) -> AuditState:
    return {
        "run_id": run_id,
        "repo_url": repo,
        "findings": [],
        "errors": [],
        "budget": budget,
        "spent": Budget(),
        "plan": [],
        "hunted_targets": 0,
        "index_ready": False,
        "sandbox_ready": False,
        "stopped_early": None,
    }


__all__ = ["NODE_ORDER", "build_graph", "initial_state", "memory_checkpointer", "open_checkpointer"]
