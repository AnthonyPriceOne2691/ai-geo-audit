"""Окружение alembic. Движок — сервисный (`build_engine("migrate")`): пояс UTC, имя в
`pg_stat_activity`, значения строк вне текста ошибок. Журнал — сервисный: `fileConfig` не
зовётся, потому что по умолчанию он выключает уже созданные логгеры, и журнал приложения после
миграции замолкал бы.
"""

from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy.engine import Connection

from ai_geo_audit.logs import setup as logs
from ai_geo_audit.storage import build_engine, models


def _migrate(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=models.Base.metadata,
        **models.COMPARE,
    )
    with context.begin_transaction():
        context.run_migrations()


async def _online() -> None:
    engine = build_engine("migrate")
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_migrate)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    raise SystemExit("миграции без базы (--sql) не поддерживаются: только по адресу базы")
logs.setup_logging()
asyncio.run(_online())
