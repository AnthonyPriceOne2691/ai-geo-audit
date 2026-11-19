"""Окружение alembic. Движок — сервисный (`build_engine("migrate")`): пояс UTC, имя в
`pg_stat_activity`, значения строк вне текста ошибок. Журнал — сервисный: `fileConfig` не
зовётся, потому что по умолчанию он выключает уже созданные логгеры, и журнал приложения после
миграции замолкал бы.

Замок миграций (A19) и пределы ожидания (A20) — внутри транзакции alembic, до чтения версии.
Команда к соединению до `context.configure` открыла бы транзакцию сама: alembic счёл бы её
внешней, `upgrade` вышел бы с кодом 0, а ревизии откатились бы при закрытии соединения.

Отказы, которые выносит сам процесс, — словами, без трассировки: проверка старта, пределы
ожидания замка и таблицы, негодные настройки, режим `--sql`. Настройки собираются при импорте
конфига, поэтому негодная настройка ловится на импорте: имя переменной и причина. Причина
строится из `errors(include_input=False)`, введённого значения pydantic в ней нет. Значение
бывает только в тексте своей проверки числа («…а не 0»). У секретных полей проверок при импорте
нет, и их значения в отказ не попадают.

Трассировкой библиотеки остаются сбои вне этих проверок: адрес базы, который не разбирается (в
dev и test проверка старта молчит), и недоступная база. Секрета в них нет: SQLAlchemy не
повторяет адрес, который не разобрал, а ошибка соединения называет хост и порт без пароля —
проба проверяющего 2026-11-19 метки пароля в таком выводе не нашла.
"""

from __future__ import annotations

import asyncio
import time

from alembic import context
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError

try:
    from ai_geo_audit import config
    from ai_geo_audit.config import startup_checks
    from ai_geo_audit.logs import setup as logs
    from ai_geo_audit.storage import build_engine, locks, models
except ValidationError as invalid:
    reasons = "\n".join(
        f"  {'.'.join(map(str, error['loc']))} — {error['msg'].removeprefix('Value error, ')}"
        for error in invalid.errors(include_url=False, include_input=False)
    )
    raise SystemExit(f"Процесс «migrate» не стартует, негодные настройки:\n{reasons}") from None

#: Предел ожидания блокировки таблицы на время ревизий — константа без настройки (decisions).
TABLE_WAIT_SECONDS = 10
#: Код Postgres «не дождался блокировки» — и у замка миграций, и у таблицы.
_LOCK_NOT_AVAILABLE = "55P03"


class MigrationRefused(Exception):
    """Прогон не начат или прерван пределом ожидания; текст — для оператора."""


def _timed_out(exc: DBAPIError) -> bool:
    return getattr(exc.orig, "sqlstate", None) == _LOCK_NOT_AVAILABLE


def _lock_timeout(connection: Connection, seconds: int) -> None:
    """`lock_timeout` до конца транзакции: на команду замка — предел замка, дальше — таблицы."""
    statement = text("SELECT set_config('lock_timeout', :value, true)")
    connection.execute(statement, {"value": f"{seconds}s"})


def _migrate(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=models.Base.metadata,
        **models.COMPARE,
    )
    limit = config.storage.migration_lock_wait_seconds
    with context.begin_transaction():
        _lock_timeout(connection, limit)
        started = time.monotonic()
        lock = text("SELECT pg_advisory_xact_lock(:key)")
        try:
            connection.execute(lock, {"key": locks.MIGRATIONS})
        except DBAPIError as exc:
            if not _timed_out(exc):
                raise
            raise MigrationRefused(
                f"миграции уже идут: ждали {time.monotonic() - started:.0f} с, предел {limit} с"
                " (MIGRATION_LOCK_WAIT_SECONDS); кто держит замок — запрос в README"
            ) from None
        _lock_timeout(connection, TABLE_WAIT_SECONDS)
        try:
            context.run_migrations()
        except DBAPIError as exc:
            if not _timed_out(exc):
                raise
            raise MigrationRefused(
                f"таблицу держит другая транзакция дольше {TABLE_WAIT_SECONDS} с: ревизии не"
                " применены, база на прежней ревизии"
            ) from None


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
try:
    startup_checks.check_startup("migrate")
    asyncio.run(_online())
except (startup_checks.ConfigError, MigrationRefused) as refused:
    raise SystemExit(str(refused)) from None
