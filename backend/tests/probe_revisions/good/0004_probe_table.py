"""Ревизия-проба: создаёт таблицу probe_rev, откат сносит её."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table("probe_rev", sa.Column("id", sa.BigInteger, primary_key=True))


def downgrade() -> None:
    op.drop_table("probe_rev")
