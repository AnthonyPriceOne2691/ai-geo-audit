"""Начало цепочки ревизий: таблиц ещё нет, первые придут с Частью 3.

Ревизия 0001. Создана 2026-11-18.
"""

from __future__ import annotations

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Таблиц нет: ревизия только заводит цепочку."""


def downgrade() -> None:
    """Таблиц нет: откатывать нечего."""
