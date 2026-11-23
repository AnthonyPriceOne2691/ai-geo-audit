"""Движки базы. Движок процесса — для процесса с одним циклом событий (API): ленивый, один на
процесс, помнит свой цикл. Задача воркера RQ сама зовёт `asyncio.run`, а соединения пула
привязаны к циклу: ей — `task_engine()`, свой малый пул и закрытие в конце.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Iterator
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
    перед выдачей. Параметры запроса в ошибках и журнале не печатаются, значения строки из DETAIL
    ошибок Postgres вырезаются, у ошибок класса данных — и значения в самом сообщении (A26).
    `DB_ECHO_SQL` пишет запросы через общий журнал. `pool_size=0` — без пула: соединение на каждую
    сессию.
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


#: Значение в сообщении ошибки класса данных: в кавычках — у сервера и в причине у драйвера;
#: без кавычек — `repr` аргумента у драйвера (число, дата) между номером и скобкой причины.
_QUOTED = (re.compile(r'"[^"]*"'), re.compile(r"'[^']*'"))
_ARGUMENT = re.compile(r"(invalid input for query argument \$\d+: ).*?( \()")


def _cut_values(text: str) -> str:
    text = _ARGUMENT.sub(r"\g<1>***\g<2>", text)
    return _QUOTED[1].sub("'***'", _QUOTED[0].sub('"***"', text))


def _chain(error: BaseException | None) -> Iterator[BaseException]:
    seen: set[int] = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        yield error
        error = error.__cause__ or error.__context__


def _without_row_values(context: ExceptionContext) -> None:
    """Значения строки — не в текст исключений: DETAIL ошибки Postgres снимается,
    `constraint_name` цел. У ошибки класса данных (SQLSTATE 22…) значение стоит в самом
    сообщении сервера или драйвера: по всей цепочке вырезается всё в кавычках и значение
    аргумента, вид ошибки и тип или номер аргумента остаются."""
    error = context.original_exception.__cause__
    if getattr(error, "detail", None):
        error.detail = None  # type: ignore[union-attr]
    if not str(getattr(context.original_exception, "sqlstate", "") or "").startswith("22"):
        return
    for exc in (context.sqlalchemy_exception, *_chain(context.original_exception)):
        if exc is None:
            continue
        exc.args = tuple(_cut_values(a) if isinstance(a, str) else a for a in exc.args)
        if isinstance(getattr(exc, "message", None), str):
            exc.message = _cut_values(exc.message)  # type: ignore[attr-defined]


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


# Проба A282 «Скорости CI»: метка области гейта мутаций, в main не идёт.
