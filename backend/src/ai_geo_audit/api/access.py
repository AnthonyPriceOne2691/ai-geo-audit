"""Ворота входа: путь вне поимённого списка открытых — только с годным пропуском.

Пропуск читается только из `X-Access-Token`: `Authorization` занят паролем сайта, адрес оседает
в журналах. Список открытых — точные строки с причинами, без шаблонов, и зависит от режима: в бою
— одно здоровье, в разработке и тестах — ещё схема и две консоли API (A51). Вошедшего ворота кладут
в состояние запроса; зависимость закрытого роутера берёт его оттуда, нет его — `signed-out`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import Request, Security
from fastapi.security import APIKeyHeader

from ai_geo_audit import config
from ai_geo_audit.api.contract import Refusal

TOKEN_HEADER = "X-Access-Token"  # noqa: S105 -- имя заголовка, не секрет
#: Открытые без пропуска пути — точной строкой и с причиной.
OPEN_PATHS: dict[str, str] = {
    "/api/health": "здоровье: его спрашивают сторож и проверка контейнера, у них нет пропуска",
}
SCHEMA_PATH = "/api/openapi.json"
SWAGGER_PATH = "/api/docs"
REDOC_PATH = "/api/redoc"
#: Схема и консоли API — открыты только в разработке и тестах; в бою их нет (A51).
CONSOLE_PATHS: dict[str, str] = {
    SCHEMA_PATH: "схема API: её читают консоли разработки; в бою её нет",
    SWAGGER_PATH: "консоль API: только в разработке и тестах; в бою её нет",
    REDOC_PATH: "вторая консоль API: только в разработке и тестах; в бою её нет",
}


def consoles_on() -> bool:
    """Схема и консоли API — в `dev` и `test`, в `prod` их нет: «настройкой» из решения
    проверяющего читается режим `APP_ENV`, своей настройки нет."""
    return config.app.env != "prod"


def open_paths() -> dict[str, str]:
    """Список открытых по режиму: в бою — одно здоровье, иначе — ещё схема и две консоли."""
    return OPEN_PATHS | CONSOLE_PATHS if consoles_on() else dict(OPEN_PATHS)


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
