"""Маршруты-пробы и сборка приложения для тестов API (A42, A44, A45).

Закрытая проба отвечает именем вошедшего, проба с телом — по схеме с запретом лишних полей,
падающая проба падает с меткой. Подставные проверки пропуска: одна признаёт тестовый пропуск,
другая падает. Схема тела — на уровне модуля: FastAPI ищет аннотации по имени, схему внутри
функции он не найдёт (проба 7 среза 0). Пропуск и пароль сайта — из слов-заглушек.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from typing import Annotated, Any

import dbtools
import httpx
from fastapi import APIRouter, FastAPI, Security

from ai_geo_audit.api import access
from ai_geo_audit.api.app import create_app
from ai_geo_audit.api.contract import RequestModel

PASS = "eyJplaceholder.test-pass-for-gate.not-a-signature"
BASIC = "Basic " + base64.b64encode(b"user:proxy-pass-words").decode()
CLOSED = "/api/probe/closed"
BODY = "/api/probe/body"
BOOM = "/api/probe/boom"
#: Вызовы закрытой пробы: по ним видно, дошёл ли запрос до обработчика.
CALLS: list[str] = []
SAMPLES = json.loads((dbtools.ROOT / "contracts" / "api-refusals.json").read_text("utf-8"))


def sample(kind: str) -> dict[str, Any]:
    """Образец вида из файла договора — раздела сервиса `samples`."""
    return next(s for s in SAMPLES["samples"] if s["id"] == kind)


class ProbeBody(RequestModel):
    name: str
    limit: int


probes = APIRouter(prefix="/api/probe", dependencies=[Security(access.signed_in)])


@probes.get("/closed")
async def closed(
    visitor: Annotated[access.Visitor, Security(access.signed_in)],
) -> dict[str, str]:
    CALLS.append(visitor.name)
    return {"visitor": visitor.name}


@probes.post("/body")
async def body(item: ProbeBody) -> dict[str, str]:
    return {"name": item.name}


@probes.get("/boom")
async def boom() -> None:
    raise RuntimeError("marker-c0ffee")


async def known_pass(token: str) -> access.Visitor | None:
    """Подставная проверка: признаёт только тестовый пропуск."""
    return access.Visitor("probe-visitor") if token == PASS else None


async def broken_check(token: str) -> access.Visitor | None:
    """Подставная проверка, которая падает: вход не должен открыться."""
    raise RuntimeError("check-down-7a1")


def build(
    check: access.VisitorCheck = known_pass, open_paths: Mapping[str, str] | None = None
) -> FastAPI:
    """Приложение сервиса с маршрутами-пробами; список открытых — свой, если задан."""
    app = create_app(visitor_for=check, open_paths=open_paths or access.OPEN_PATHS)
    app.include_router(probes)
    return app


def client(app: FastAPI, **transport: Any) -> httpx.AsyncClient:
    """ASGI-клиент в том же цикле событий; адрес соединения — петля."""
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 40000), **transport),
        base_url="http://test",
    )
