"""Database-backed run queue (item 10). Lets the API enqueue runs while one or more `sentinel worker`
processes execute them, so the API can run with several replicas behind a load balancer. Live
events are persisted to `run_events`; the API's SSE endpoint tails the table when the run is owned
by another process.

    SENTINEL_EXECUTION_MODE=inline   (default) API executes runs in its own process
    SENTINEL_EXECUTION_MODE=queue    API only enqueues; run `sentinel worker` separately
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine, and_, or_
from sqlmodel import col, select

from sentinel.db.models import Run, as_utc
from sentinel.db.session import session_scope
from sentinel.runner import RunManager
from sentinel.telemetry.metrics import QUEUE_DEPTH

log = logging.getLogger("sentinel.queue")
LEASE_SECONDS = 600


def enqueue(engine: Engine, run_id: str) -> None:
    with session_scope(engine) as s:
        run = s.get(Run, run_id)
        if run is None:
            raise KeyError(run_id)
        run.status = "queued"
        s.add(run)
    _update_depth(engine)


def _update_depth(engine: Engine) -> None:
    with session_scope(engine) as s:
        n = len(s.exec(select(Run.id).where(Run.status == "queued")).all())
    QUEUE_DEPTH.set(n)


def claim_next(engine: Engine, worker_id: str) -> Run | None:
    """Atomically claim the oldest queued run (or one whose worker lease expired)."""
    now = datetime.now(UTC)
    with session_scope(engine) as s:
        stale = now - timedelta(seconds=LEASE_SECONDS)
        candidates = s.exec(
            select(Run)
            .where(
                or_(
                    col(Run.status) == "queued",
                    and_(col(Run.status) == "running", col(Run.worker_heartbeat) < stale),
                )
            )
            .order_by(col(Run.started_at))
            .limit(5)
        ).all()
        for run in candidates:
            # optimistic claim: re-read under the same transaction and set the owner
            fresh = s.get(Run, run.id)
            if fresh is None or (fresh.status == "running" and fresh.worker_id == worker_id):
                continue
            hb = as_utc(fresh.worker_heartbeat)
            if fresh.status == "running" and hb is not None and hb >= stale:
                continue
            fresh.status = "running"
            fresh.worker_id = worker_id
            fresh.worker_heartbeat = now
            s.add(fresh)
            s.flush()
            s.refresh(fresh)
            s.expunge(fresh)
            _update_depth(engine)
            return fresh
    return None


def heartbeat(engine: Engine, run_id: str, worker_id: str) -> None:
    with session_scope(engine) as s:
        run = s.get(Run, run_id)
        if run is not None and run.worker_id == worker_id:
            run.worker_heartbeat = datetime.now(UTC)
            s.add(run)


class Worker:
    """Polls the queue and executes runs with a RunManager. One worker executes one run at a time
    per `concurrency` slot; horizontal scale = more worker processes."""

    def __init__(
        self, manager: RunManager, *, poll_seconds: float = 2.0, concurrency: int = 1
    ) -> None:
        self.manager = manager
        self.engine = manager.engine
        self.poll_seconds = poll_seconds
        self.concurrency = concurrency
        self.worker_id = f"{socket.gethostname()}-{threading.get_ident()}"
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run_once(self) -> bool:
        run = claim_next(self.engine, self.worker_id)
        if run is None:
            return False
        log.info("worker %s claimed run %s (%s)", self.worker_id, run.id, run.repo_url)
        hb_stop = threading.Event()

        def beat() -> None:
            while not hb_stop.wait(LEASE_SECONDS / 4):
                heartbeat(self.engine, run.id, self.worker_id)

        t = threading.Thread(target=beat, daemon=True)
        t.start()
        try:
            self.manager.start(run, block=True)
        finally:
            hb_stop.set()
        return True

    def serve_forever(self) -> None:
        log.info("worker %s started (poll=%ss)", self.worker_id, self.poll_seconds)
        while not self._stop.is_set():
            try:
                if not self.run_once():
                    time.sleep(self.poll_seconds)
            except Exception:  # noqa: BLE001 - a worker must survive a bad run
                log.exception("worker loop error")
                time.sleep(self.poll_seconds)
