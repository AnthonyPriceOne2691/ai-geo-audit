"""Здоровье сервиса: `GET /api/health` без пропуска — отвечает ли база и принимает ли Redis запись
(A38–A41, A243).

Ответ — одно слово: ни версий, ни адресов, ни ревизии миграций. База — своим соединением, а не
сессией из зависимости: зависимость упала бы до тела, и здоровье ответило бы 500 вместо 503 ровно
тогда, когда должно сказать «база недоступна». Не ответила в срок — 503 `unavailable` формой
договора и запись WARNING с классом исключения и пределом, без текста ошибки драйвера: в нём
адрес и имя базы.

Срок один на весь поход в базу — соединение, запрос и закрытие (A40). Соединение — новое на
каждую проверку, движком без пула: пул выдал бы соединение, замёрзшее вместе с базой, а его
проверка перед выдачей и вежливое закрытие ждут базу вне предела. Не уложились — соединение
обрывается в срок, а не закрывается вежливо.

Redis проверяется разом с базой, под той же границей, и не `PING`, а записью своего ключа со
сроком: полный под `noeviction` Redis на `PING` отвечает `PONG`, а запись — и постановку тревоги —
отвергает (слово владельца 2026-11-25T12:39:26Z, проба среза 0). Соединение — своё, без повторов
клиента; в срок оно обрывается без ожидания закрытия (L45). Запись WARNING — с классом исключения,
у отказа соединения — с классом причины (`ConnectionRefusedError`), как у базы; без адреса и пароля.
"""

from __future__ import annotations

import asyncio
import logging

import redis.asyncio as aredis
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from redis import exceptions as redis_errors
from redis.backoff import NoBackoff
from redis.retry import Retry
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from ai_geo_audit import config
from ai_geo_audit.api.contract import refusal_response
from ai_geo_audit.storage import build_engine

#: Предел ожидания базы и Redis, секунды. Защита, а не настройка: решение владельца о пределах.
HEALTH_DB_LIMIT_SECONDS = 3.0
#: Ключ пробы Redis записью — свой, со сроком: проба не копит ключей.
REDIS_PROBE_KEY = "ai-geo-audit:health:probe"
REDIS_PROBE_TTL_MS = 60_000

log = logging.getLogger(__name__)
router = APIRouter()
#: Движки здоровья по адресу базы: без пула, соединений между проверками не держат.
_engines: dict[str, AsyncEngine] = {}


@router.get("/api/health")
async def health() -> JSONResponse:
    """200 `{"status": "ok"}`, если база ответила на `SELECT 1`, а Redis принял пробу записью —
    оба в срок; иначе 503 и по записи WARNING на каждого, кто не ответил."""
    limit = HEALTH_DB_LIMIT_SECONDS
    outcomes = await asyncio.gather(_select_one(limit), _redis_write(limit), return_exceptions=True)
    failed = False
    for message, outcome in zip(
        ("здоровье: база не ответила", "здоровье: Redis не ответил"), outcomes, strict=True
    ):
        if isinstance(outcome, Exception):
            log.warning(message, extra={"error": _error_name(outcome), "limit_seconds": limit})
            failed = True
    return refusal_response("unavailable") if failed else JSONResponse({"status": "ok"})


def _error_name(failure: Exception) -> str:
    """Класс для записи; у отказа соединения клиента `redis` — класс причины, как у базы: клиент
    кладёт её в `__cause__` или, поднимая без `from`, в `__context__`."""
    cause = failure.__cause__ or failure.__context__
    if isinstance(failure, redis_errors.ConnectionError) and isinstance(cause, OSError):
        return type(cause).__name__
    return type(failure).__name__


async def _redis_write(limit: float) -> None:
    """Проба записью своего ключа со сроком своим соединением; в срок — обрыв без ожидания."""
    client = aredis.Redis.from_url(
        config.storage.redis_url.get_secret_value(),
        single_connection_client=True,
        retry=Retry(NoBackoff(), 0),
    )
    try:
        async with asyncio.timeout(limit):
            await client.set(REDIS_PROBE_KEY, "1", px=REDIS_PROBE_TTL_MS)
    finally:
        if client.connection is not None:
            await client.connection.disconnect(nowait=True)
        await client.aclose()


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
