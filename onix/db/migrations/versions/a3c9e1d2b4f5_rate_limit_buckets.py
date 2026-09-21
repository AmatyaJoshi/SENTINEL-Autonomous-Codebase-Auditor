"""rate limit buckets shared across API replicas

Revision ID: a3c9e1d2b4f5
Revises: f026627a61c2
Create Date: 2026-09-14 10:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
import sqlmodel
import sqlmodel.sql.sqltypes  # noqa: F401
from alembic import op

revision = "a3c9e1d2b4f5"
down_revision = "f026627a61c2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if "rate_limit_buckets" in sa.inspect(op.get_bind()).get_table_names():
        return  # created by create_all on a legacy database that was stamped, then upgraded
    op.create_table(
        "rate_limit_buckets",
        sa.Column("key", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("window", sa.Integer(), nullable=False),
        sa.Column("hits", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("key", "window"),
    )


def downgrade() -> None:
    op.drop_table("rate_limit_buckets")
