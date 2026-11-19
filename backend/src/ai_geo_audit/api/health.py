"""Здоровье сервиса: `GET /api/health` без пропуска — отвечает ли база (A38–A41).

Ответ — одно слово: ни версий, ни адресов, ни ревизии миграций. База — своим соединением движка
процесса, а не сессией из зависимости: зависимость упала бы до тела, и здоровье ответило бы 500
вместо 503 ровно тогда, когда должно сказать «база недоступна». Не ответила за предел — 503
`unavailable` формой договора и запись WARNING с классом исключения и пределом, без текста ошибки
драйвера: в нём адрес и имя базы.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from ai_geo_audit.api.contract import refusal_response
from ai_geo_audit.storage import get_engine

#: Предел ожидания базы, секунды. Защита, а не настройка: решение владельца о пределах входа.
HEALTH_DB_LIMIT_SECONDS = 3.0

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/health")
async def health() -> JSONResponse:
    """200 `{"status": "ok"}`, если база ответила на `SELECT 1` за предел; иначе 503."""
    limit = HEALTH_DB_LIMIT_SECONDS
    try:
        async with asyncio.timeout(limit), get_engine().connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as failure:  # noqa: BLE001 -- любой сбой базы — 503 и запись, без текста
        log.warning(
            "здоровье: база не ответила",
            extra={"error": type(failure).__name__, "limit_seconds": limit},
        )
        return refusal_response("unavailable")
    return JSONResponse({"status": "ok"})
