"""Приложение API и настройки его сервера.

`create_app()` — фабрика для uvicorn: проверка старта `api` — до сборки; схема и консоли API — в
`dev` и `test`, в бою их нет, маршрута oauth2-redirect нет ни в одном режиме (A51). Собранное
приложение несёт в `app.state.open_paths` тот же список открытых, что его ворота. Остановка
закрывает движок процесса: соединения пула не живут до конца процесса (A37). `server_settings()`
— все поля uvicorn явно: своё окружение (`FORWARDED_ALLOW_IPS`, `WEB_CONCURRENCY`) uvicorn тогда
не читает.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException

from ai_geo_audit import config
from ai_geo_audit.api import access, contract, health
from ai_geo_audit.api.middleware import EntryLayer
from ai_geo_audit.config.startup_checks import check_startup
from ai_geo_audit.logs.setup import setup_logging
from ai_geo_audit.storage import dispose_engine

#: Мягкая остановка: запросы в работе доживают до этого предела, затем процесс кончается.
GRACEFUL_SHUTDOWN_SECONDS = 20


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Остановка закрывает движок процесса (A37): иначе соединения пула живут до конца процесса."""
    yield
    await dispose_engine()


def create_app(
    *,
    visitor_for: access.VisitorCheck = access.visitor_for,
    open_paths: Mapping[str, str] | None = None,
) -> FastAPI:
    """Собрать приложение; в бою без нужных настроек — `ConfigError` до сборки (A32). Журнал
    настраивает само, повторная сборка заменяет обработчик, а не добавляет второй (A36).

    Проверку пропуска и список открытых тесты передают свои; без своего — список по режиму.
    `swagger_ui_oauth2_redirect_url` — `None` всегда: умолчание FastAPI ставит маршрут на
    `/docs/oauth2-redirect` при любом `docs_url` (проба 10)."""
    check_startup("api")
    setup_logging()
    consoles = access.consoles_on()
    app = FastAPI(
        openapi_url=access.SCHEMA_PATH if consoles else None,
        docs_url=access.SWAGGER_PATH if consoles else None,
        redoc_url=access.REDOC_PATH if consoles else None,
        swagger_ui_oauth2_redirect_url=None,
        lifespan=_lifespan,
    )
    listed = dict(access.open_paths() if open_paths is None else open_paths)
    app.state.open_paths = listed
    app.add_exception_handler(HTTPException, contract.on_http_exception)
    app.add_exception_handler(RequestValidationError, contract.on_validation_error)
    app.include_router(health.router)
    app.add_middleware(EntryLayer, visitor_for=visitor_for, open_paths=listed)
    return app


def server_settings() -> dict[str, object]:
    """Настройки uvicorn одной функцией (A34); адреса прокси — из `API_TRUSTED_PROXIES`."""
    api = config.api
    return {
        "app": "ai_geo_audit.api.app:create_app",
        "factory": True,
        "host": api.host,
        "port": api.port,
        "loop": "asyncio",
        "http": "h11",
        "ws": "none",
        "lifespan": "on",
        "log_config": None,
        "access_log": False,
        "proxy_headers": True,
        "forwarded_allow_ips": api.trusted_proxies,
        "server_header": False,
        "timeout_keep_alive": 5,
        "timeout_graceful_shutdown": GRACEFUL_SHUTDOWN_SECONDS,
        "workers": 1,
    }
