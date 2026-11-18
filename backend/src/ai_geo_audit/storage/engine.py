"""Движки базы: движок процесса и движок задачи воркера.

Движок процесса годится для процесса с одним циклом событий на всю жизнь (API): он ленивый,
один на процесс и помнит цикл, в котором создан. Задача воркера RQ сама зовёт `asyncio.run`,
то есть живёт в новом цикле, а соединения пула привязаны к старому. Ей — `task_engine()`: свой
малый пул на время задачи и закрытие в конце.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from ai_geo_audit import config

#: Запас пула задачи сверх постоянных соединений.
TASK_MAX_OVERFLOW = 1

_process: dict[str, tuple[AsyncEngine, asyncio.AbstractEventLoop]] = {}


class ForeignLoopError(RuntimeError):
    """Движок процесса взят из другого цикла событий."""


def build_engine(
    name: str,
    *,
    url: str | None = None,
    pool_size: int = 0,
    max_overflow: int = 0,
    storage: config.StorageSettings | None = None,
) -> AsyncEngine:
    """Движок с правилами соединения сервиса.

    Имя приложения `<сервис>:<name>` видно в `pg_stat_activity`; пояс соединения — UTC, а не
    сервера; из пула соединение выдаётся после проверки. Значения параметров запросов не
    попадают ни в текст ошибки, ни в журнал: дальше в них почты, хеши паролей и ключи.
    `DB_ECHO_SQL` включает журнал запросов через общий обработчик, а не через свой вывод
    SQLAlchemy. `pool_size=0` — без пула: соединение на каждую сессию.
    """
    storage = storage or config.storage
    if storage.echo_sql:
        logging.getLogger("sqlalchemy.engine.Engine").setLevel(logging.INFO)
    pool: dict[str, object] = (
        {"pool_size": pool_size, "max_overflow": max_overflow, "pool_pre_ping": True}
        if pool_size
        else {"poolclass": NullPool}
    )
    settings = {"application_name": f"{config.app.service_name}:{name}", "timezone": "UTC"}
    return create_async_engine(
        url or storage.database_url.get_secret_value(),
        hide_parameters=True,
        connect_args={"server_settings": settings},
        **pool,
    )


def get_engine(process: str = "api") -> AsyncEngine:
    """Движок процесса: один на процесс, создаётся при первом вызове внутри цикла событий."""
    loop = asyncio.get_running_loop()
    if "engine" not in _process:
        storage = config.storage
        engine = build_engine(
            process, pool_size=storage.pool_size, max_overflow=storage.max_overflow
        )
        _process["engine"] = (engine, loop)
    engine, home = _process["engine"]
    if home is not loop:
        raise ForeignLoopError(
            "движок процесса создан в другом цикле событий — "
            "в задаче воркера бери движок задачи (task_engine)"
        )
    return engine


async def dispose_engine() -> None:
    """Закрыть движок процесса и забыть его: конец процесса или конец теста."""
    held = _process.pop("engine", None)
    if held is not None:
        await held[0].dispose()


@asynccontextmanager
async def task_engine(name: str = "task") -> AsyncIterator[AsyncEngine]:
    """Движок задачи воркера: свой малый пул на время задачи, закрытие в конце, даже при сбое."""
    storage = config.storage
    engine = build_engine(name, pool_size=storage.task_pool_size, max_overflow=TASK_MAX_OVERFLOW)
    try:
        yield engine
    finally:
        await engine.dispose()
