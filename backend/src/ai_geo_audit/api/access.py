"""Ворота входа: путь вне поимённого списка открытых — только с годным пропуском.

Пропуск читается только из `X-Access-Token`: `Authorization` занят паролем сайта, адрес оседает
в журналах. Список открытых — точные строки с причинами, без шаблонов. Вошедшего ворота кладут в
состояние запроса; зависимость закрытого роутера берёт его оттуда, нет его — `signed-out`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import Request, Security
from fastapi.security import APIKeyHeader

from ai_geo_audit.api.contract import Refusal

TOKEN_HEADER = "X-Access-Token"  # noqa: S105 -- имя заголовка, не секрет
#: Открытые без пропуска пути — точной строкой и с причиной.
OPEN_PATHS: dict[str, str] = {
    "/api/health": "здоровье: его спрашивают сторож и проверка контейнера, у них нет пропуска",
}


@dataclass(frozen=True, slots=True)
class Visitor:
    """Вошедший: кого признала проверка пропуска."""

    name: str


VisitorCheck = Callable[[str], Awaitable[Visitor | None]]


async def visitor_for(token: str) -> Visitor | None:
    """Проверка пропуска. До Ч1.8 пропусков нет: не признаётся ни один."""
    del token
    return None


#: Схема входа для схемы API: заголовок пропуска. Сама проверка — в воротах.
_pass_header = APIKeyHeader(name=TOKEN_HEADER, auto_error=False)


async def signed_in(request: Request, _token: str | None = Security(_pass_header)) -> Visitor:
    """Зависимость закрытого роутера: вошедший — из состояния запроса; нет его — отказ."""
    visitor = getattr(request.state, "visitor", None)
    if not isinstance(visitor, Visitor):
        raise Refusal("signed-out")
    return visitor
