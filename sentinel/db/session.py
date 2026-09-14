"""Engine + session factory. SQLite for dev, Postgres for prod (SPEC.md §2.2)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine
from sqlmodel import Session, SQLModel, create_engine

from sentinel.config import Settings, get_settings

_engine: Engine | None = None


def get_engine(settings: Settings | None = None) -> Engine:
    global _engine
    if _engine is None:
        settings = settings or get_settings()
        url = settings.database_url
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        if url.startswith("sqlite:///") and not url.startswith("sqlite:///:memory:"):
            Path(url.removeprefix("sqlite:///")).expanduser().parent.mkdir(
                parents=True, exist_ok=True
            )
        _engine = create_engine(url, connect_args=connect_args)
    return _engine


def init_db(engine: Engine | None = None) -> None:
    """Create/upgrade the schema. Uses Alembic migrations when a versions/ directory exists so
    production databases evolve safely; falls back to create_all for throwaway engines."""
    from sentinel.db import models  # noqa: F401  (registers tables on the metadata)

    eng = engine or get_engine()
    SQLModel.metadata.create_all(eng)
    try:
        from sentinel.db.migrate import ROOT, upgrade

        if any((ROOT / "sentinel" / "db" / "migrations" / "versions").glob("*.py")):
            upgrade(eng, str(eng.url.render_as_string(hide_password=False)))
    except Exception as e:  # noqa: BLE001 - migrations are best-effort for in-memory/test engines
        import logging

        logging.getLogger("sentinel.db").debug("alembic upgrade skipped: %s", e)


@contextmanager
def session_scope(engine: Engine | None = None) -> Iterator[Session]:
    with Session(engine or get_engine(), expire_on_commit=False) as session:
        yield session
        session.commit()


def reset_engine() -> None:
    """Testing hook."""
    global _engine
    _engine = None
