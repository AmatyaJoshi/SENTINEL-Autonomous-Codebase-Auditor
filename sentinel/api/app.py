"""FastAPI application implementing docs/API.md: auth (API keys + roles), rate limiting, audit log,
runs/findings/events (SSE), bench results, reports, and the built dashboard as static files."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlmodel import col, select
from sse_starlette.sse import EventSourceResponse

from sentinel import __version__
from sentinel.config import Role, Settings
from sentinel.db.models import AuditLog, BenchResult, FindingRecord
from sentinel.db.session import get_engine, session_scope
from sentinel.graph.state import Budget
from sentinel.runner import RunManager, run_to_dict

log = logging.getLogger("sentinel.api")
ROLE_RANK = {"viewer": 0, "operator": 1, "admin": 2}
DASHBOARD_DIST = Path(__file__).parent.parent.parent / "dashboard" / "dist"


class Principal(BaseModel):
    name: str
    role: Role


class ApiError(HTTPException):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(status_code=status_code, detail={"code": code, "message": message})


class _RateLimiter:
    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> float | None:
        now = time.time()
        q = self._hits[key]
        while q and q[0] < now - 60:
            q.popleft()
        if len(q) >= self.per_minute:
            return 60 - (now - q[0])
        q.append(now)
        return None


class CreateRun(BaseModel):
    repo: str = Field(min_length=1)
    sha: str | None = None
    open_pr: bool = False
    review: bool = False
    max_usd: float = Field(default=5.0, ge=0)
    max_minutes: int = Field(default=60, ge=1)
    max_findings: int = Field(default=25, ge=1)
    arm: Literal["full", "no_triage", "single_shot", "analyzers"] = "full"


class Decision(BaseModel):
    decision: Literal["approve", "reject"]
    note: str = ""


def create_app(settings: Settings, manager: RunManager | None = None) -> FastAPI:
    engine = manager.engine if manager is not None else get_engine(settings)
    mgr = manager or RunManager(settings, engine)
    limiter = _RateLimiter(settings.api_rate_limit_per_minute)
    open_mode = not settings.api_keys
    if open_mode:
        log.warning(
            "SENTINEL_API_KEYS not set: API running in OPEN dev mode (every request is admin)"
        )
    keys = {
        k.key.get_secret_value(): Principal(name=k.name, role=k.role) for k in settings.api_keys
    }

    app = FastAPI(
        title="Sentinel", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json"
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.api_cors_origins,
        allow_methods=["*"],
        allow_headers=["*", "X-API-Key"],
        allow_credentials=False,
    )

    @app.exception_handler(ApiError)
    async def _api_error(_req: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})

    @app.exception_handler(HTTPException)
    async def _http_error(_req: Request, exc: HTTPException) -> JSONResponse:
        detail = (
            exc.detail
            if isinstance(exc.detail, dict)
            else {"code": "error", "message": str(exc.detail)}
        )
        return JSONResponse(
            status_code=exc.status_code, content={"error": detail}, headers=exc.headers
        )

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        resp: Response = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("Cache-Control", "no-store") if request.url.path.startswith(
            "/api"
        ) else None
        return resp

    # ------------------------------------------------------------------ auth
    def principal(request: Request) -> Principal:
        key = request.headers.get("X-API-Key") or request.query_params.get("api_key")
        if open_mode:
            who = Principal(name="dev", role="admin")
        else:
            if not key:
                raise ApiError(401, "unauthorized", "missing X-API-Key")
            who = keys.get(key)  # type: ignore[assignment]
            if who is None:
                raise ApiError(401, "unauthorized", "invalid API key")
        wait = limiter.check(key or request.client.host if request.client else "anon")
        if wait is not None:
            raise HTTPException(
                429,
                {"code": "rate_limited", "message": "too many requests"},
                headers={"Retry-After": str(int(wait) + 1)},
            )
        return who

    def require(role: Role):  # type: ignore[no-untyped-def]
        def dep(who: Principal = Depends(principal)) -> Principal:
            if ROLE_RANK[who.role] < ROLE_RANK[role]:
                raise ApiError(403, "forbidden", f"requires role {role}")
            return who

        return dep

    def audit(who: Principal, action: str, target: str | None = None, **detail: Any) -> None:
        with session_scope(engine) as s:
            s.add(
                AuditLog(actor=who.name, role=who.role, action=action, target=target, detail=detail)
            )

    def get_run_or_404(run_id: str) -> dict[str, Any]:
        run = mgr.get(run_id)
        if run is None:
            raise ApiError(404, "not_found", f"run {run_id} not found")
        return run_to_dict(run)

    # ------------------------------------------------------------------ health
    @app.get("/health")
    def health() -> dict[str, Any]:
        docker = "unavailable"
        try:
            sb = mgr.sandbox_factory()
            docker = "ok" if sb is not None else "unavailable"
        except Exception:  # noqa: BLE001
            docker = "unavailable"
        try:
            with session_scope(engine) as s:
                s.exec(select(FindingRecord.id).limit(1)).first()
            db = "ok"
        except Exception:  # noqa: BLE001
            db = "error"
        return {
            "status": "ok" if db == "ok" else "degraded",
            "version": __version__,
            "checks": {
                "db": db,
                "docker": docker,
                "llm": "configured" if settings.llm_configured() else "missing",
                "auth": "open" if open_mode else "api-key",
            },
        }

    @app.get("/ready")
    def ready() -> Response:
        try:
            with session_scope(engine) as s:
                s.exec(select(FindingRecord.id).limit(1)).first()
            return JSONResponse({"ready": True})
        except Exception:  # noqa: BLE001
            return JSONResponse({"ready": False}, status_code=503)

    @app.get("/api/v1/me")
    def me(who: Principal = Depends(principal)) -> Principal:
        return who

    # ------------------------------------------------------------------ stats
    @app.get("/api/v1/stats")
    def stats(_: Principal = Depends(principal)) -> dict[str, Any]:
        runs, total = mgr.list_runs(limit=1000)
        with session_scope(engine) as s:
            frs = s.exec(select(FindingRecord)).all()
            latest = s.exec(
                select(BenchResult)
                .where(BenchResult.arm == "full")
                .order_by(col(BenchResult.created_at).desc())
            ).first()
        by_cat: dict[str, int] = defaultdict(int)
        by_status: dict[str, int] = defaultdict(int)
        for f in frs:
            by_cat[f.category] += 1
            by_status[f.status] += 1
        return {
            "runs_total": total,
            "runs_active": sum(1 for r in runs if r.status in ("running", "awaiting_review")),
            "findings_total": len(frs),
            "verified_total": by_status["verified"]
            + by_status["fixed"]
            + by_status["pr_opened"]
            + by_status["regressed"],
            "fixed_total": by_status["fixed"] + by_status["pr_opened"],
            "pr_opened_total": by_status["pr_opened"],
            "cost_usd_total": round(sum(r.cost_usd for r in runs), 4),
            "precision_latest": latest.precision if latest else None,
            "by_category": dict(by_cat),
            "by_status": dict(by_status),
        }

    # ------------------------------------------------------------------ runs
    @app.get("/api/v1/runs")
    def list_runs(
        limit: int = 50,
        offset: int = 0,
        status: str | None = None,
        _: Principal = Depends(principal),
    ) -> dict[str, Any]:
        runs, total = mgr.list_runs(limit=min(limit, 200), offset=offset, status=status)
        return {"items": [run_to_dict(r) for r in runs], "total": total}

    @app.post("/api/v1/runs", status_code=202)
    def create_run(
        body: CreateRun, who: Principal = Depends(require("operator"))
    ) -> dict[str, Any]:
        if mgr.active_count() >= settings.max_concurrent_runs:
            raise ApiError(409, "conflict", "max concurrent runs reached; try later")
        run = mgr.create(
            body.repo,
            budget=Budget(
                max_usd=body.max_usd, max_minutes=body.max_minutes, max_findings=body.max_findings
            ),
            arm=body.arm,
            open_pr=body.open_pr,
            review=body.review,
            created_by=who.name,
            sha=body.sha,
        )
        mgr.start(run)
        audit(who, "run.create", run.id, repo=body.repo, arm=body.arm)
        return get_run_or_404(run.id)

    @app.get("/api/v1/runs/{run_id}")
    def get_run(run_id: str, _: Principal = Depends(principal)) -> dict[str, Any]:
        return get_run_or_404(run_id)

    @app.post("/api/v1/runs/{run_id}/cancel")
    def cancel_run(run_id: str, who: Principal = Depends(require("operator"))) -> dict[str, Any]:
        get_run_or_404(run_id)
        mgr.cancel(run_id)
        audit(who, "run.cancel", run_id)
        return get_run_or_404(run_id)

    @app.post("/api/v1/runs/{run_id}/resume")
    def resume_run(
        run_id: str, from_node: str | None = None, who: Principal = Depends(require("operator"))
    ) -> dict[str, Any]:
        get_run_or_404(run_id)
        try:
            mgr.resume(run_id, from_node=from_node)
        except (KeyError, ValueError) as e:
            raise ApiError(409, "conflict", str(e)) from e
        audit(who, "run.resume", run_id, from_node=from_node)
        return get_run_or_404(run_id)

    @app.delete("/api/v1/runs/{run_id}", status_code=204)
    def delete_run(run_id: str, who: Principal = Depends(require("admin"))) -> Response:
        if not mgr.delete(run_id):
            raise ApiError(404, "not_found", f"run {run_id} not found")
        audit(who, "run.delete", run_id)
        return Response(status_code=204)

    # ------------------------------------------------------------------ findings
    def finding_dict(f: FindingRecord) -> dict[str, Any]:
        d = f.model_dump()
        d["created_at"] = f.created_at.isoformat()
        return d

    @app.get("/api/v1/runs/{run_id}/findings")
    def list_findings(
        run_id: str,
        status: str | None = None,
        category: str | None = None,
        min_confidence: float = 0.0,
        _: Principal = Depends(principal),
    ) -> dict[str, Any]:
        get_run_or_404(run_id)
        items = [
            finding_dict(f)
            for f in mgr.findings(run_id)
            if (not status or f.status == status)
            and (not category or f.category == category)
            and f.confidence >= min_confidence
        ]
        return {"items": items}

    @app.get("/api/v1/runs/{run_id}/findings/{fid}")
    def get_finding(run_id: str, fid: str, _: Principal = Depends(principal)) -> dict[str, Any]:
        for f in mgr.findings(run_id):
            if f.id == fid:
                return finding_dict(f)
        raise ApiError(404, "not_found", "finding not found")

    @app.post("/api/v1/runs/{run_id}/findings/{fid}/decision")
    def decide(
        run_id: str, fid: str, body: Decision, who: Principal = Depends(require("operator"))
    ) -> dict[str, Any]:
        run = get_run_or_404(run_id)
        if run["status"] != "awaiting_review":
            raise ApiError(409, "conflict", "run is not awaiting review")
        try:
            updated = mgr.decide(run_id, fid, body.decision, body.note)
        except KeyError as e:
            raise ApiError(409, "conflict", str(e)) from e
        if updated is None:
            raise ApiError(404, "not_found", "finding not found")
        audit(who, f"finding.{body.decision}", fid, run_id=run_id, note=body.note)
        return updated.model_dump()

    # ------------------------------------------------------------------ events (SSE)
    @app.get("/api/v1/runs/{run_id}/events")
    async def events(
        run_id: str, request: Request, _: Principal = Depends(principal)
    ) -> EventSourceResponse:
        get_run_or_404(run_id)
        last = request.headers.get("Last-Event-ID") or request.query_params.get("after") or "0"
        after = int(last) if last.isdigit() else 0
        queue: asyncio.Queue[tuple[int, str, dict[str, Any]]] = asyncio.Queue()
        loop = asyncio.get_running_loop()
        bus = mgr.buses.get(run_id)

        def _push(seq: int, t: str, d: dict[str, Any]) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, (seq, t, d))

        unsub = bus.subscribe(_push) if bus else None

        async def gen() -> AsyncIterator[dict[str, Any]]:
            try:
                seq_seen = after
                for ev in mgr.events(run_id, after_seq=after):
                    seq_seen = ev.seq
                    yield {
                        "id": str(ev.seq),
                        "event": ev.type,
                        "data": json.dumps(ev.data, default=str),
                    }
                    if ev.type == "run.end":
                        return
                run = mgr.get(run_id)
                if run and run.status in ("completed", "failed", "cancelled") and bus is None:
                    return
                while True:
                    if await request.is_disconnected():
                        return
                    try:
                        seq, t, d = await asyncio.wait_for(queue.get(), timeout=15)
                    except TimeoutError:
                        yield {"comment": "ping"}
                        continue
                    if seq <= seq_seen:
                        continue
                    seq_seen = seq
                    yield {"id": str(seq), "event": t, "data": json.dumps(d, default=str)}
                    if t == "run.end":
                        return
            finally:
                if unsub:
                    unsub()

        return EventSourceResponse(gen())

    # ------------------------------------------------------------------ reports
    @app.get("/api/v1/runs/{run_id}/report.{fmt}")
    def report(run_id: str, fmt: str, _: Principal = Depends(principal)) -> Response:
        get_run_or_404(run_id)
        if fmt not in ("html", "json", "md"):
            raise ApiError(404, "not_found", "unknown report format")
        p = settings.work_dir / "runs" / run_id / f"report.{fmt}"
        if not p.exists():
            raise ApiError(404, "not_found", "report not generated yet")
        media = {"html": "text/html", "json": "application/json", "md": "text/markdown"}[fmt]
        return FileResponse(p, media_type=media)

    # ------------------------------------------------------------------ bench
    @app.get("/api/v1/bench/results")
    def bench_results(
        suite: str | None = None, limit: int = 20, _: Principal = Depends(principal)
    ) -> dict[str, Any]:
        with session_scope(engine) as s:
            q = select(BenchResult).where(BenchResult.mutation_id.is_(None))  # type: ignore[union-attr]
            if suite:
                q = q.where(BenchResult.suite == suite)
            rows = s.exec(
                q.order_by(col(BenchResult.created_at).desc()).limit(min(limit, 100))
            ).all()
            items = []
            for r in rows:
                m = dict(r.metrics or {})
                items.append(
                    {
                        "id": r.id,
                        "created_at": r.created_at.isoformat(),
                        "commit": r.commit_sha,
                        "suite": r.suite,
                        "arm": r.arm,
                        "precision": r.precision,
                        "precision_strict": m.get("precision_strict"),
                        "recall": r.recall,
                        "f1": r.f1,
                        "verified_rate": m.get("verified_rate"),
                        "patch_pass_rate": m.get("patch_pass_rate"),
                        "cost_usd": r.cost_usd,
                        "wall_clock_s": r.wall_clock_s,
                        "per_category": m.get("per_category", {}),
                        "arms": m.get("arms", {}),
                    }
                )
        return {"items": items}

    @app.get("/api/v1/settings")
    def settings_view(_: Principal = Depends(require("admin"))) -> dict[str, Any]:
        return settings.redacted()

    # ------------------------------------------------------------------ dashboard
    if DASHBOARD_DIST.exists():
        app.mount("/assets", StaticFiles(directory=DASHBOARD_DIST / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str) -> Response:
            if full_path.startswith(("api/", "health", "ready")):
                raise ApiError(404, "not_found", "no such route")
            candidate = DASHBOARD_DIST / full_path
            if full_path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(DASHBOARD_DIST / "index.html")
    else:

        @app.get("/", include_in_schema=False)
        def root() -> dict[str, str]:
            return {
                "service": "sentinel",
                "version": __version__,
                "docs": "/api/docs",
                "note": "dashboard not built; run `npm run build` in dashboard/",
            }

    app.state.manager = mgr
    return app


def app_factory() -> FastAPI:
    from sentinel.config import get_settings

    return create_app(get_settings())
