"""Ревизия-проба: меняет таблицу probe_target, которую в тесте держит чужая транзакция (A20)."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("probe_target", sa.Column("extra", sa.Integer))


def downgrade() -> None:
    op.drop_column("probe_target", "extra")
