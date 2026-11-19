"""Типизированный конфиг — единственный вход к настройкам; `os.getenv` вне пакета запрещён.

Экземпляры создаются при импорте: конфиг читается на старте, и ошибка видна там же.
"""

from __future__ import annotations

from ai_geo_audit.config._base import Settings
from ai_geo_audit.config.domains import (
    AccessSettings,
    ApiSettings,
    AppSettings,
    KeySettings,
    LogSettings,
    StorageSettings,
)

app = AppSettings()
storage = StorageSettings()
access = AccessSettings()
keys = KeySettings()
logs = LogSettings()
api = ApiSettings()

DOMAINS: tuple[type[Settings], ...] = (
    AppSettings,
    StorageSettings,
    AccessSettings,
    KeySettings,
    LogSettings,
    ApiSettings,
)


def env_names() -> list[str]:
    """Имена всех переменных окружения, которые читает конфиг."""
    return sorted(str(f.validation_alias) for d in DOMAINS for f in d.model_fields.values())


__all__ = [
    "DOMAINS",
    "AccessSettings",
    "ApiSettings",
    "AppSettings",
    "KeySettings",
    "LogSettings",
    "Settings",
    "StorageSettings",
    "access",
    "api",
    "app",
    "env_names",
    "keys",
    "logs",
    "storage",
]
