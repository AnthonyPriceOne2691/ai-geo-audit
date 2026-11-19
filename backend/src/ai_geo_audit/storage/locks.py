"""Ключи advisory-замков Postgres — в одном месте: два замка с одним ключом ждали бы друг друга
молча. Тест A19 сверяет, что ключи различны.
"""

from __future__ import annotations

from typing import Final

#: Замок миграций: `migrations/env.py` держит его на время прогона alembic (A19).
MIGRATIONS: Final = 1001

REGISTRY: Final[dict[str, int]] = {"migrations": MIGRATIONS}
