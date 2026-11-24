"""Маршруты-пробы и сборка приложения для тестов API (A36–A54).

Закрытая проба отвечает именем вошедшего, проба с телом — по схеме с запретом лишних полей,
падающая проба падает с меткой, проба журнала пишет запись INFO. Проба заголовков пишет в журнал
`Authorization`, пропуск и пароль сайта, как небрежный код; вторая падающая проба падает с
пропуском в тексте (A48). Проба чтения читает базу сессией движка процесса (A37). Проба
ожидания держит вошедших до события теста; открытая проба с телом стоит в списке открытых, как
будущий вход по паролю (A53, A54); проба своих заголовков ставит кэш, `nosniff` и номер запроса
сама (A49). Приложение боя — `APP_ENV=prod` с настройками из слов-заглушек, как в A32 (A51).
Приложения с одним дефектом обхода и одно без дефектов — для A52.
Подставные проверки пропуска: одна признаёт тестовый пропуск, другая падает. Схема тела — на
уровне модуля: FastAPI ищет аннотации по имени, схему внутри функции он не найдёт (проба 7 среза
0, L43). Пропуск и пароль сайта — из слов-заглушек.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import socket
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any

import dbtools
import httpx
import masterkeys
import pytest
from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    HTTPException,
    Request,
    Security,
    WebSocket,
)
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader, HTTPBearer, OAuth2PasswordBearer
from pydantic import BaseModel, SecretStr
from sqlalchemy import text
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.types import ASGIApp

from ai_geo_audit import config
from ai_geo_audit.api import access
from ai_geo_audit.api.app import create_app
from ai_geo_audit.api.contract import RequestModel
from ai_geo_audit.storage import session_scope

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
DB_READ = "/api/probe/db-read"
WAIT = "/api/probe/wait"
OPEN_BODY = "/api/probe/open-body"
OPEN_ECHO = "/api/probe/open-echo"
OWN_HEADERS = "/api/probe/own-headers"
#: Вызовы закрытой пробы: по ним видно, дошёл ли запрос до обработчика.
CALLS: list[str] = []
SAMPLES = json.loads((dbtools.ROOT / "contracts" / "api-refusals.json").read_text("utf-8"))


def sample(kind: str) -> dict[str, Any]:
    """Образец из файла договора по `id` — раздела сервиса `samples` или раздела прокси `proxy`."""
    return next(
        s for part in ("samples", "proxy") for s in SAMPLES.get(part, []) if s["id"] == kind
    )


class ProbeBody(RequestModel):
    name: str
    limit: int


class Loose(BaseModel):
    """Тело без запрета лишних полей — дефект обхода (A52)."""

    name: str = ""


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
    scheme, _, value = authorization.partition(" ")
    pair = base64.b64decode(value).decode() if scheme.lower() == "basic" else ""
    logging.getLogger("ai_geo_audit.api.probe").info(
        "заголовки: %s",
        authorization,
        extra={"token": request.headers.get("x-access-token"), "password": pair.partition(":")[2]},
    )
    return {}


@probes.get("/db-read")
async def db_read() -> dict[str, int]:
    """Читает базу сессией движка процесса: соединение из пула (A37)."""
    async with session_scope() as session:
        value = (await session.execute(text("SELECT 1"))).scalar_one()
    return {"value": int(value)}


@probes.get("/boom-pass")
async def boom_pass(request: Request) -> None:
    raise RuntimeError(f"упал с пропуском {request.headers.get('x-access-token')}")


class Waiting:
    """Проба ожидания: считает вошедших и держит их до события теста (A54)."""

    def __init__(self) -> None:
        self.entered = 0
        self.release = asyncio.Event()
        self._changed = asyncio.Event()

    def enter(self) -> None:
        self.entered += 1
        self._changed.set()

    async def until(self, count: int) -> None:
        """Ждать, пока вошедших станет `count`, — событием, без сна."""
        while self.entered < count:
            self._changed.clear()
            await self._changed.wait()


@probes.get("/own-headers")
async def own_headers() -> JSONResponse:
    """Маршрут сам ставит кэш, `nosniff` и номер запроса — слой обязан их заменить (A49)."""
    mine = {
        "Cache-Control": "public, max-age=60",
        "X-Content-Type-Options": "sniff",
        "X-Request-ID": "forged-id-1",
    }
    return JSONResponse({}, headers=mine)


#: Проба ожидания текущего теста: тест ставит свою, события не переходят между циклами.
WAITING = Waiting()


@probes.get("/wait")
async def wait() -> dict[str, int]:
    WAITING.enter()
    await WAITING.release.wait()
    return {"entered": WAITING.entered}


#: Открытые пробы — без зависимости входа, как будущий вход по паролю.
open_probes = APIRouter(prefix="/api/probe")


@open_probes.post("/open-body")
async def open_body(item: ProbeBody) -> dict[str, str]:
    return {"name": item.name}


@open_probes.get("/open-echo")
async def open_echo(request: Request) -> dict[str, str]:
    """Пишет присланный `X-Access-Token` в журнал текстом кода (A48)."""
    logging.getLogger("ai_geo_audit.api.probe").info(
        "пришло: %s", request.headers.get("x-access-token")
    )
    return {}


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


def build_open_body() -> FastAPI:
    """Приложение, где проба с телом стоит в списке открытых, как будущий вход по паролю (A53)."""
    app = build(
        open_paths={
            **access.OPEN_PATHS,
            OPEN_BODY: "проба: вход по паролю",
            OPEN_ECHO: "проба: открытая запись в журнал",
        }
    )
    app.include_router(open_probes)
    return app


#: Пароли адресов баз в пробах — из слов-заглушек; в адрес они подставляются.
CLOSED_WORDS = "probe-words-123456"
PROD_DB_WORDS = "prod-db-words-123456"
PROD_JWT_WORDS = "prod-jwt-from-plain-words-for-tests-only"


def closed_port_url() -> str:
    """Адрес базы на петле, где никто не слушает."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"postgresql+asyncpg://audit:{CLOSED_WORDS}@127.0.0.1:{port}/audit"


def prod_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Боевой режим с настройками из слов-заглушек, как в A32: проверка старта `api` проходит."""
    key = masterkeys.write(tmp_path / "master.key", [masterkeys.line()])
    env = {
        "APP_ENV": "prod",
        "DATABASE_URL": f"postgresql+asyncpg://audit:{PROD_DB_WORDS}@db:5432/audit",
        "REDIS_URL": "redis://redis:6379/0",
        "JWT_SECRET": PROD_JWT_WORDS,
        "KEYS_MASTER_KEY_FILE": str(key),
        "API_TRUSTED_PROXIES": "10.9.9.9",
    }
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    for name, domain in (
        ("app", config.AppSettings),
        ("storage", config.StorageSettings),
        ("access", config.AccessSettings),
        ("keys", config.KeySettings),
        ("api", config.ApiSettings),
    ):
        monkeypatch.setattr(config, name, domain())


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


DEFECT = "/api/defect"
#: Маршруты без дефекта: включённый роутер, роутер в роутере, `Mount`, скрытая схема, тело.
CLEAN_PATHS = (
    "/api/clean/flat",
    "/api/clean/inner/deep",
    "/api/clean-mount/z",
    "/api/clean/hidden",
    "/api/clean/body",
)


async def ok() -> dict[str, str]:
    return {}


async def plain(_request: Request) -> PlainTextResponse:
    return PlainTextResponse("ok")


async def raise_401() -> None:
    raise HTTPException(401)


async def loose_body(item: Loose) -> dict[str, str]:
    return {"name": item.name}


async def clean_body(item: ProbeBody) -> dict[str, str]:
    return {"name": item.name}


async def socket_probe(socket: WebSocket) -> None:
    await socket.close()


def defect_app(name: str) -> FastAPI:
    """Приложение с одним дефектом обхода (A52); остальное — как у `create_app()`."""
    if name == "empty":
        bare = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
        bare.state.open_paths = {}
        return bare
    listed = access.open_paths()
    router = APIRouter()
    path = f"{DEFECT}/{name}"
    if name == "bearer":
        router.add_api_route(path, ok, dependencies=[Depends(HTTPBearer())])
    elif name == "oauth2":
        scheme = OAuth2PasswordBearer(tokenUrl="/api/x")
        router.add_api_route(path, ok, dependencies=[Depends(scheme)])
    elif name == "api-key-open":
        router.add_api_route(path, ok, dependencies=[Depends(APIKeyHeader(name="X-Access-Token"))])
        listed[path] = "проба: будущий вход по паролю"
    elif name == "raise-401":
        router.add_api_route(path, raise_401)
    elif name == "loose-body":
        router.add_api_route(path, loose_body, methods=["POST"])
    elif name == "list-typo":
        listed["/api/heath"] = "проба: опечатка в списке"
    elif name == "websocket":
        router.add_api_websocket_route(path, socket_probe)
    app = create_app(visitor_for=known_pass, open_paths=listed)
    app.include_router(router)
    return app


def clean_app() -> FastAPI:
    """Приложение без дефектов: маршруты во включённом роутере, в роутере, включённом в роутер,
    внутри `Mount`, со скрытой схемой и с телом на базе схем запросов (A52)."""
    outer = APIRouter(prefix="/api/clean")
    inner = APIRouter(prefix="/inner")
    inner.add_api_route("/deep", ok)
    outer.add_api_route("/flat", ok)
    outer.add_api_route("/hidden", ok, include_in_schema=False)
    outer.add_api_route("/body", clean_body, methods=["POST"])
    outer.include_router(inner)
    app = create_app(visitor_for=known_pass)
    app.include_router(outer)
    app.mount("/api/clean-mount", Starlette(routes=[Route("/z", plain)]))
    return app
