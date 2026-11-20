"""Маршруты-пробы и сборка приложения для тестов API (A36, A38–A48).

Закрытая проба отвечает именем вошедшего, проба с телом — по схеме с запретом лишних полей,
падающая проба падает с меткой, проба журнала пишет запись INFO. Проба заголовков пишет в журнал
`Authorization`, пропуск и пароль сайта, как небрежный код; вторая падающая проба падает с
пропуском в тексте (A48). Подставные проверки пропуска: одна признаёт тестовый пропуск, другая
падает. Схема тела — на уровне модуля: FastAPI ищет аннотации по имени, схему внутри функции он
не найдёт (проба 7 среза 0, L43). Пропуск и пароль сайта — из слов-заглушек.
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Mapping
from typing import Annotated, Any

import dbtools
import httpx
import pytest
from fastapi import APIRouter, FastAPI, HTTPException, Request, Security
from pydantic import SecretStr
from starlette.types import ASGIApp

from ai_geo_audit import config
from ai_geo_audit.api import access
from ai_geo_audit.api.app import create_app
from ai_geo_audit.api.contract import RequestModel

PASS = "eyJplaceholder.test-pass-for-gate.not-a-signature"
BASIC = "Basic " + base64.b64encode(b"user:proxy-pass-words").decode()
CLOSED = "/api/probe/closed"
BODY = "/api/probe/body"
BOOM = "/api/probe/boom"
UNAUTHORIZED = "/api/probe/unauthorized"
BARE_503 = "/api/probe/bare-503"
JOURNAL = "/api/probe/journal"
HEADERS = "/api/probe/headers"
BOOM_PASS = "/api/probe/boom-pass"
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


@probes.get("/unauthorized")
async def unauthorized() -> None:
    raise HTTPException(401, headers={"WWW-Authenticate": "Bearer"})


@probes.get("/bare-503")
async def bare_503() -> None:
    raise HTTPException(503, detail="bare-503-mark-5e1f")


@probes.get("/journal")
async def journal() -> dict[str, str]:
    logging.getLogger("ai_geo_audit.api.probe").info("проба", extra={"probe": 7})
    return {}


@probes.get("/headers")
async def headers(request: Request) -> dict[str, str]:
    """Пишет заголовки входа в журнал; пароль сайта — из `Basic`, как его разобрал бы код."""
    authorization = request.headers.get("authorization", "")
    pair = base64.b64decode(authorization.partition(" ")[2]).decode()
    logging.getLogger("ai_geo_audit.api.probe").info(
        "заголовки: %s",
        authorization,
        extra={"token": request.headers.get("x-access-token"), "password": pair.partition(":")[2]},
    )
    return {}


@probes.get("/boom-pass")
async def boom_pass(request: Request) -> None:
    raise RuntimeError(f"упал с пропуском {request.headers.get('x-access-token')}")


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


def use_database(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    """Движок процесса смотрит в эту базу: настройка подменяется до первого вызова движка."""
    storage = config.StorageSettings()
    monkeypatch.setattr(storage, "database_url", SecretStr(url))
    monkeypatch.setattr(config, "storage", storage)


def client(
    app: ASGIApp, address: tuple[str, int] = ("127.0.0.1", 40000), **transport: Any
) -> httpx.AsyncClient:
    """ASGI-клиент в том же цикле событий; адрес соединения — петля, если не задан свой."""
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=address, **transport),
        base_url="http://test",
    )
