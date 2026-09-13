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
    from sentinel.db import models  # noqa: F401  (registers tables on the metadata)

    SQLModel.metadata.create_all(engine or get_engine())


@contextmanager
def session_scope(engine: Engine | None = None) -> Iterator[Session]:
    with Session(engine or get_engine(), expire_on_commit=False) as session:
        yield session
        session.commit()


def reset_engine() -> None:
    """Testing hook."""
    global _engine
    _engine = None
