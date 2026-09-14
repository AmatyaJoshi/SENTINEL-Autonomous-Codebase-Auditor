"""Alembic environment (item 15). URL comes from Sentinel settings; metadata from SQLModel."""

from __future__ import annotations

from logging.config import fileConfig

import sqlmodel  # noqa: F401 - needed so autogenerate resolves sqlmodel.sql.sqltypes
from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

from sentinel.config import get_settings
from sentinel.db import models  # noqa: F401 - registers tables

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata
config.set_main_option(
    "sqlalchemy.url", config.attributes.get("sentinel_url") or get_settings().database_url
)


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=connection.dialect.name == "sqlite",  # ALTER TABLE support on SQLite
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
