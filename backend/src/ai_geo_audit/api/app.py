"""Приложение API и настройки его сервера.

`create_app()` — фабрика для uvicorn: проверка старта `api` — до сборки; схема и консоль API
выключены во всех режимах, консоль разработки — Ч1.3б. `server_settings()` — все поля uvicorn
явно: своё окружение (`FORWARDED_ALLOW_IPS`, `WEB_CONCURRENCY`) uvicorn тогда не читает.
"""

from __future__ import annotations

from fastapi import FastAPI

from ai_geo_audit import config
from ai_geo_audit.config.startup_checks import check_startup

#: Мягкая остановка: запросы в работе доживают до этого предела, затем процесс кончается.
GRACEFUL_SHUTDOWN_SECONDS = 20


def create_app() -> FastAPI:
    """Собрать приложение; в бою без нужных настроек — `ConfigError` до сборки (A32)."""
    check_startup("api")
    return FastAPI(
        openapi_url=None, docs_url=None, redoc_url=None, swagger_ui_oauth2_redirect_url=None
    )


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
