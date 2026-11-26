"""Ревизия-проба: записывает, какой `lock_timeout` действует внутри ревизии (A20)."""

from __future__ import annotations

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("CREATE TABLE probe_setting AS SELECT current_setting('lock_timeout') AS value")


def downgrade() -> None:
    op.drop_table("probe_setting")
