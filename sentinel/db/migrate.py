"""Programmatic Alembic entrypoints used by `sentinel db upgrade` and by init_db()."""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

ROOT = Path(__file__).resolve().parent.parent.parent
log = logging.getLogger("sentinel.db")


def alembic_config(database_url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "sentinel" / "db" / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    cfg.attributes["sentinel_url"] = database_url
    return cfg


def current_revision(engine: Engine) -> str | None:
    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def head_revision(database_url: str) -> str | None:
    return ScriptDirectory.from_config(alembic_config(database_url)).get_current_head()


def upgrade(engine: Engine, database_url: str, revision: str = "head") -> None:
    """Apply migrations. A database created by the old `create_all` path (no alembic_version) is
    stamped at head first, since its schema already matches."""
    cfg = alembic_config(database_url)
    cfg.attributes["connection"] = None
    cur = current_revision(engine)
    from sqlalchemy import inspect

    tables = set(inspect(engine).get_table_names())
    if cur is None and "runs" in tables and "alembic_version" not in tables:
        log.info("existing schema without alembic history: stamping head")
        command.stamp(cfg, "head")
        return
    command.upgrade(cfg, revision)


def downgrade(database_url: str, revision: str) -> None:
    command.downgrade(alembic_config(database_url), revision)


def autogenerate(database_url: str, message: str) -> None:
    command.revision(alembic_config(database_url), message=message, autogenerate=True)
