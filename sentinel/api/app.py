"""FastAPI application implementing docs/API.md: auth (API keys, DB keys, OIDC), roles, rate limiting,
audit log, request IDs, Prometheus metrics, runs/findings/events (SSE, cross-process), reports,
bench results, key management, and the built dashboard as static files."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import threading
import time
import uuid
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Callable
from datetime import timedelta
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
from sentinel.api.auth import ROLE_RANK, Authenticator, Principal
from sentinel.config import Role, Settings
from sentinel.db.models import AuditLog, BenchResult, FindingRecord
from sentinel.db.session import get_engine, session_scope
from sentinel.graph.state import Budget
from sentinel.runner import RunManager, run_to_dict
from sentinel.telemetry import metrics

log = logging.getLogger("sentinel.api")
DASHBOARD_DIST = Path(__file__).parent.parent.parent / "dashboard" / "dist"
_ = timedelta  # re-exported for type checkers in auth helpers


class ApiError(HTTPException):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(status_code=status_code, detail={"code": code, "message": message})


class _RateLimiter:
    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> float | None:
        now = time.time()
        with self._lock:
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


class CreateKey(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    role: Role = "viewer"
    expires_days: int | None = Field(default=None, ge=1, le=3650)


def create_app(settings: Settings, manager: RunManager | None = None) -> FastAPI:
    engine = manager.engine if manager is not None else get_engine(settings)
    mgr = manager or RunManager(settings, engine)
    auth = Authenticator(settings, engine)
    limiter = _RateLimiter(settings.api_rate_limit_per_minute)
    if auth.open_mode:
        log.warning(
            "no API keys or OIDC issuer configured: API running in OPEN dev mode (every request is admin)"
        )
    if settings.gc_on_startup and settings.retention_days > 0:
        from sentinel.retention import collect_garbage

        threading.Thread(
            target=lambda: collect_garbage(settings, engine), name="gc-startup", daemon=True
        ).start()

    app = FastAPI(
        title="Sentinel", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json"
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.api_cors_origins,
        allow_methods=["*"],
        allow_headers=["*", "X-API-Key", "Authorization", "X-Request-ID"],
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
    async def _observe(request: Request, call_next: Callable[[Request], Any]) -> Response:
        rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        t0 = time.perf_counter()
        resp: Response = await call_next(request)
        route = request.scope.get("route")
        path = getattr(route, "path", request.url.path)
        if not path.startswith("/assets"):
            metrics.HTTP_REQUESTS.labels(request.method, path, str(resp.status_code)).inc()
            metrics.HTTP_LATENCY.labels(request.method, path).observe(time.perf_counter() - t0)
        resp.headers["X-Request-ID"] = rid
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.path.startswith("/api"):
            resp.headers.setdefault("Cache-Control", "no-store")
            log.info(
                "%s %s %s %.0fms rid=%s",
                request.method,
                request.url.path,
                resp.status_code,
                (time.perf_counter() - t0) * 1000,
                rid,
            )
        return resp

    # ------------------------------------------------------------------ auth
    def principal(request: Request) -> Principal:
        who: Principal | None = None
        bearer = request.headers.get("Authorization", "")
        key = request.headers.get("X-API-Key") or request.query_params.get("api_key")
        if bearer.lower().startswith("bearer "):
            who = auth.by_bearer(bearer[7:].strip())
            if who is None and not key:
                raise ApiError(401, "unauthorized", "invalid bearer token")
        if who is None and key:
            who = auth.by_api_key(key)
            if who is None:
                raise ApiError(401, "unauthorized", "invalid API key")
        if who is None:
            if auth.open_mode:
                who = Principal(name="dev", role="admin", via="open")
            else:
                raise ApiError(401, "unauthorized", "missing X-API-Key or Authorization: Bearer")
        client = request.client.host if request.client else "anon"
        wait = limiter.check(key or bearer[-32:] or client)
        if wait is not None:
            raise HTTPException(
                429,
                {"code": "rate_limited", "message": "too many requests"},
                headers={"Retry-After": str(int(wait) + 1)},
            )
        return who

    def require(role: Role) -> Callable[..., Principal]:
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

    # ------------------------------------------------------------------ health / metrics
    @app.get("/health")
    def health() -> dict[str, Any]:
        docker = "unavailable"
        with contextlib.suppress(Exception):
            docker = "ok" if mgr.sandbox_factory() is not None else "unavailable"
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
                "auth": "open"
                if auth.open_mode
                else ("oidc+api-key" if settings.oidc_issuer else "api-key"),
                "execution": settings.execution_mode,
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

    @app.get("/metrics", include_in_schema=False)
    def prometheus() -> Response:
        body, ctype = metrics.render()
        return Response(content=body, media_type=ctype)

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
            "runs_active": sum(
                1 for r in runs if r.status in ("running", "queued", "awaiting_review")
            ),
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
        if settings.execution_mode == "queue":
            from sentinel.queue import enqueue

            enqueue(engine, run.id)
        else:
            if mgr.active_count() >= settings.max_concurrent_runs:
                mgr.delete(run.id)
                raise ApiError(
                    409, "conflict", "max concurrent runs reached; try later or enable queue mode"
                )
            mgr.start(run)
        audit(who, "run.create", run.id, repo=body.repo, arm=body.arm, mode=settings.execution_mode)
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

        def _fmt(seq: int, t: str, d: Any) -> dict[str, Any]:
            return {"id": str(seq), "event": t, "data": json.dumps(d, default=str)}

        async def gen() -> AsyncIterator[dict[str, Any]]:
            try:
                seq_seen = after
                for ev in mgr.events(run_id, after_seq=after):
                    seq_seen = ev.seq
                    yield _fmt(ev.seq, ev.type, ev.data)
                    if ev.type == "run.end":
                        return
                while True:
                    if await request.is_disconnected():
                        return
                    if bus is not None:  # run owned by this process: push
                        try:
                            seq, t, d = await asyncio.wait_for(queue.get(), timeout=15)
                        except TimeoutError:
                            yield {"comment": "ping"}
                            continue
                        if seq <= seq_seen:
                            continue
                        seq_seen = seq
                        yield _fmt(seq, t, d)
                        if t == "run.end":
                            return
                    else:  # run owned by a worker / other replica: tail the events table
                        new = await asyncio.to_thread(mgr.events, run_id, seq_seen)
                        if not new:
                            run = mgr.get(run_id)
                            if run and run.status in ("completed", "failed", "cancelled"):
                                return
                            await asyncio.sleep(1.0)
                            yield {"comment": "ping"}
                            continue
                        for ev in new:
                            seq_seen = ev.seq
                            yield _fmt(ev.seq, ev.type, ev.data)
                            if ev.type == "run.end":
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

    # ------------------------------------------------------------------ admin: settings, keys, gc, audit
    @app.get("/api/v1/settings")
    def settings_view(_: Principal = Depends(require("admin"))) -> dict[str, Any]:
        return settings.redacted()

    def key_dict(k: Any) -> dict[str, Any]:
        return {
            "id": k.id,
            "name": k.name,
            "role": k.role,
            "prefix": k.prefix,
            "created_by": k.created_by,
            "created_at": k.created_at.isoformat(),
            "expires_at": k.expires_at.isoformat() if k.expires_at else None,
            "revoked_at": k.revoked_at.isoformat() if k.revoked_at else None,
            "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
        }

    @app.get("/api/v1/keys")
    def list_keys(_: Principal = Depends(require("admin"))) -> dict[str, Any]:
        return {"items": [key_dict(k) for k in auth.list_keys()]}

    @app.post("/api/v1/keys", status_code=201)
    def create_key(body: CreateKey, who: Principal = Depends(require("admin"))) -> dict[str, Any]:
        raw, rec = auth.create_key(body.name, body.role, who.name, body.expires_days)
        audit(who, "key.create", rec.id, name=body.name, role=body.role)
        return {**key_dict(rec), "key": raw, "note": "store this key now; it is not shown again"}

    @app.post("/api/v1/keys/{key_id}/rotate")
    def rotate_key(key_id: str, who: Principal = Depends(require("admin"))) -> dict[str, Any]:
        out = auth.rotate_key(key_id, who.name)
        if out is None:
            raise ApiError(404, "not_found", "key not found or already revoked")
        raw, rec = out
        audit(who, "key.rotate", key_id, new_id=rec.id)
        return {**key_dict(rec), "key": raw, "note": "old key stays valid for 1 hour"}

    @app.delete("/api/v1/keys/{key_id}", status_code=204)
    def revoke_key(key_id: str, who: Principal = Depends(require("admin"))) -> Response:
        if not auth.revoke_key(key_id):
            raise ApiError(404, "not_found", "key not found or already revoked")
        audit(who, "key.revoke", key_id)
        return Response(status_code=204)

    @app.post("/api/v1/admin/gc")
    def run_gc(
        older_than_days: int | None = None,
        dry_run: bool = False,
        who: Principal = Depends(require("admin")),
    ) -> dict[str, Any]:
        from sentinel.retention import collect_garbage

        rep = collect_garbage(settings, engine, older_than_days=older_than_days, dry_run=dry_run)
        audit(who, "admin.gc", None, older_than_days=older_than_days, dry_run=dry_run)
        return {k: v for k, v in rep.__dict__.items() if k != "details"} | {
            "details": rep.details[:50]
        }

    @app.get("/api/v1/audit-log")
    def audit_log(limit: int = 100, _: Principal = Depends(require("admin"))) -> dict[str, Any]:
        with session_scope(engine) as s:
            rows = s.exec(
                select(AuditLog).order_by(col(AuditLog.at).desc()).limit(min(limit, 500))
            ).all()
            return {
                "items": [
                    {
                        "at": r.at.isoformat(),
                        "actor": r.actor,
                        "role": r.role,
                        "action": r.action,
                        "target": r.target,
                        "detail": r.detail,
                    }
                    for r in rows
                ]
            }

    # ------------------------------------------------------------------ dashboard
    if DASHBOARD_DIST.exists():
        app.mount("/assets", StaticFiles(directory=DASHBOARD_DIST / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str) -> Response:
            if full_path.startswith(("api/", "health", "ready", "metrics")):
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
    app.state.auth = auth
    return app


def app_factory() -> FastAPI:
    from sentinel.config import get_settings

    return create_app(get_settings())
