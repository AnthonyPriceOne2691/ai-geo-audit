"""Здоровье сервиса: `GET /api/health` без пропуска — отвечает ли база (A38–A41).

Ответ — одно слово: ни версий, ни адресов, ни ревизии миграций. База — своим соединением, а не
сессией из зависимости: зависимость упала бы до тела, и здоровье ответило бы 500 вместо 503 ровно
тогда, когда должно сказать «база недоступна». Не ответила в срок — 503 `unavailable` формой
договора и запись WARNING с классом исключения и пределом, без текста ошибки драйвера: в нём
адрес и имя базы.

Срок один на весь поход в базу — соединение, запрос и закрытие (A40). Соединение — новое на
каждую проверку, движком без пула: пул выдал бы соединение, замёрзшее вместе с базой, а его
проверка перед выдачей и вежливое закрытие ждут базу вне предела. Не уложились — соединение
обрывается в срок, а не закрывается вежливо.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from ai_geo_audit import config
from ai_geo_audit.api.contract import refusal_response
from ai_geo_audit.storage import build_engine

#: Предел ожидания базы, секунды. Защита, а не настройка: решение владельца о пределах входа.
HEALTH_DB_LIMIT_SECONDS = 3.0

log = logging.getLogger(__name__)
router = APIRouter()
#: Движки здоровья по адресу базы: без пула, соединений между проверками не держат.
_engines: dict[str, AsyncEngine] = {}


@router.get("/api/health")
async def health() -> JSONResponse:
    """200 `{"status": "ok"}`, если база ответила на `SELECT 1` в срок; иначе 503."""
    limit = HEALTH_DB_LIMIT_SECONDS
    try:
        await _select_one(limit)
    except Exception as failure:
        log.warning(
            "здоровье: база не ответила",
            extra={"error": type(failure).__name__, "limit_seconds": limit},
        )
        return refusal_response("unavailable")
    return JSONResponse({"status": "ok"})


async def _select_one(limit: float) -> None:
    """`SELECT 1` новым соединением; соединение — под `asyncio.timeout`, запрос и закрытие — под
    обрывом сокета в тот же срок: отмена внутри драйвера ждала бы базу. Не успели — `TimeoutError`.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + limit
    async with asyncio.timeout_at(deadline):
        connection = await _engine().connect()
    cut = _Cut(connection)
    handle = loop.call_at(deadline, cut)
    try:
        async with asyncio.timeout_at(deadline + 1):
            await connection.execute(text("SELECT 1"))
            await connection.close()
    except Exception as failure:
        if cut.fired:
            raise TimeoutError(f"база не ответила за {limit} с") from failure
        raise
    finally:
        handle.cancel()


def _engine() -> AsyncEngine:
    """Движок здоровья для адреса базы процесса: создаётся один раз, пула нет."""
    url = config.storage.database_url.get_secret_value()
    if url not in _engines:
        _engines[url] = build_engine("health")
    return _engines[url]


class _Cut:
    """Обрыв соединения в срок: сокет драйвера закрывается сразу, без обмена с базой."""

    def __init__(self, connection: AsyncConnection) -> None:
        self.connection = connection
        self.fired = False

    def __call__(self) -> None:
        self.fired = True
        sync = self.connection.sync_connection
        if sync is not None and not sync.closed:
            driver = sync.connection.driver_connection
            if driver is not None:
                driver.terminate()


# проба шага статистики mutmut после правки корня тестов выката — ветка не сливается
