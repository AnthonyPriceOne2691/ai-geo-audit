"""Ревизия-проба: создаёт таблицу probe_rev, первая из двух, проходит."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table("probe_rev", sa.Column("id", sa.BigInteger, primary_key=True))


def downgrade() -> None:
    op.drop_table("probe_rev")
