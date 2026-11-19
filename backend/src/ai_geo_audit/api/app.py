"""Приложение API и настройки его сервера.

`create_app()` — фабрика для uvicorn: проверка старта `api` — до сборки; схема и консоль API
выключены во всех режимах, консоль разработки — Ч1.3б. `server_settings()` — все поля uvicorn
явно: своё окружение (`FORWARDED_ALLOW_IPS`, `WEB_CONCURRENCY`) uvicorn тогда не читает.
"""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException

from ai_geo_audit import config
from ai_geo_audit.api import access, contract
from ai_geo_audit.api.middleware import EntryLayer
from ai_geo_audit.config.startup_checks import check_startup

#: Мягкая остановка: запросы в работе доживают до этого предела, затем процесс кончается.
GRACEFUL_SHUTDOWN_SECONDS = 20


def create_app(
    *,
    visitor_for: access.VisitorCheck = access.visitor_for,
    open_paths: Mapping[str, str] = access.OPEN_PATHS,
) -> FastAPI:
    """Собрать приложение; в бою без нужных настроек — `ConfigError` до сборки (A32).

    Проверку пропуска и список открытых тесты передают свои."""
    check_startup("api")
    app = FastAPI(
        openapi_url=None, docs_url=None, redoc_url=None, swagger_ui_oauth2_redirect_url=None
    )
    app.add_exception_handler(HTTPException, contract.on_http_exception)
    app.add_exception_handler(RequestValidationError, contract.on_validation_error)
    app.add_middleware(EntryLayer, visitor_for=visitor_for, open_paths=open_paths)
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
