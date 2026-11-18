"""Движки базы. Движок процесса — для процесса с одним циклом событий (API): ленивый, один на
процесс, помнит свой цикл. Задача воркера RQ сама зовёт `asyncio.run`, а соединения пула
привязаны к циклу: ей — `task_engine()`, свой малый пул и закрытие в конце.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event
from sqlalchemy.engine import ExceptionContext
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
    """Движок сервиса: имя `<сервис>:<name>` в `pg_stat_activity`, пояс UTC, проверка соединения
    перед выдачей; значения параметров не идут ни в ошибки, ни в журнал. `DB_ECHO_SQL` пишет
    запросы через общий журнал. `pool_size=0` — без пула: соединение на каждую сессию.
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
    engine = create_async_engine(
        url or storage.database_url.get_secret_value(),
        hide_parameters=True,
        connect_args={"server_settings": settings},
        **pool,
    )
    event.listen(engine.sync_engine, "handle_error", _without_row_values)
    return engine


def _without_row_values(context: ExceptionContext) -> None:
    """Значения строки из DETAIL ошибки Postgres — не в текст исключений; `constraint_name` цел."""
    for error in (context.original_exception, context.original_exception.__cause__):
        if getattr(error, "detail", None):
            error.detail = None  # type: ignore[union-attr]
            error.args = tuple(str(arg).split("\nDETAIL:")[0] for arg in error.args)


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
