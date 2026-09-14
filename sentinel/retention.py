"""Retention / garbage collection (item 16): `sentinel gc` and the API's periodic sweep."""

from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import Engine
from sqlmodel import col, select

from sentinel.config import Settings
from sentinel.db.models import FindingRecord, Run, RunEvent
from sentinel.db.session import session_scope

log = logging.getLogger("sentinel.retention")


@dataclass
class GcReport:
    runs_deleted: int = 0
    run_dirs_removed: int = 0
    workspaces_removed: int = 0
    clones_removed: int = 0
    events_deleted: int = 0
    bytes_freed: int = 0
    kept_runs: int = 0
    details: list[str] = field(default_factory=list)


def _dir_size(p: Path) -> int:
    total = 0
    for f in p.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            pass
    return total


def _rm(p: Path, rep: GcReport, what: str) -> None:
    if not p.exists():
        return
    size = _dir_size(p) if p.is_dir() else p.stat().st_size
    shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
    rep.bytes_freed += size
    rep.details.append(f"removed {what}: {p}")


def collect_garbage(
    settings: Settings,
    engine: Engine,
    *,
    older_than_days: int | None = None,
    keep_reports: bool = True,
    dry_run: bool = False,
    prune_clones_days: int | None = None,
) -> GcReport:
    """Delete finished runs (DB rows, events, run dirs) older than the retention window.

    Reports (report.*) are kept unless `keep_reports=False`; workspaces, sandbox build contexts
    and checkpoints of deleted runs always go. Clones under work_dir/repos not touched for
    `prune_clones_days` are removed too.
    """
    days = older_than_days if older_than_days is not None else settings.retention_days
    cutoff = datetime.now(UTC) - timedelta(days=days)
    rep = GcReport()
    runs_dir = settings.work_dir / "runs"
    with session_scope(engine) as s:
        finished = s.exec(
            select(Run).where(
                col(Run.status).in_(["completed", "failed", "cancelled"]),
                col(Run.finished_at) < cutoff,
            )
        ).all()
        rep.kept_runs = len(s.exec(select(Run.id)).all()) - len(finished)
        for run in finished:
            run_dir = runs_dir / run.id
            if not dry_run:
                for f in s.exec(select(FindingRecord).where(FindingRecord.run_id == run.id)).all():
                    s.delete(f)
                n_ev = 0
                for e in s.exec(select(RunEvent).where(RunEvent.run_id == run.id)).all():
                    s.delete(e)
                    n_ev += 1
                rep.events_deleted += n_ev
                s.delete(run)
                if run_dir.exists():
                    if keep_reports:
                        for child in run_dir.iterdir():
                            if child.name.startswith("report."):
                                continue
                            _rm(
                                child,
                                rep,
                                "workspace" if "workspace" in child.name else "run artifact",
                            )
                        rep.workspaces_removed += 1
                    else:
                        _rm(run_dir, rep, "run dir")
                        rep.run_dirs_removed += 1
            rep.runs_deleted += 1
    # workspaces for runs that vanished (crash, manual delete)
    if runs_dir.exists():
        with session_scope(engine) as s:
            live = {r for r in s.exec(select(Run.id)).all()}
        for d in runs_dir.iterdir():
            if d.is_dir() and d.name not in live and not dry_run:
                for child in d.iterdir():
                    if not child.name.startswith("report."):
                        _rm(child, rep, "orphan artifact")
                rep.workspaces_removed += 1
    # stale clones
    clones = settings.work_dir / "repos"
    prune = prune_clones_days if prune_clones_days is not None else settings.retention_days * 2
    if clones.exists():
        limit = time.time() - prune * 86400
        for d in clones.iterdir():
            try:
                if d.is_dir() and d.stat().st_mtime < limit:
                    if not dry_run:
                        _rm(d, rep, "stale clone")
                    rep.clones_removed += 1
            except OSError:
                continue
    log.info(
        "gc: runs=%d workspaces=%d clones=%d freed=%.1f MB dry_run=%s",
        rep.runs_deleted,
        rep.workspaces_removed,
        rep.clones_removed,
        rep.bytes_freed / 1e6,
        dry_run,
    )
    return rep
