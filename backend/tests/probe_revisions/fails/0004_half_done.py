"""Ревизия-проба: таблица уже создана, потом сбой — база должна остаться на прежней ревизии."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table("probe_half", sa.Column("id", sa.BigInteger, primary_key=True))
    raise RuntimeError("проба: сбой посередине ревизии")


def downgrade() -> None:
    op.drop_table("probe_half")
