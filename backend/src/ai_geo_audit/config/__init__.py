"""Типизированный конфиг — единственный вход к настройкам; `os.getenv` вне пакета запрещён.

Экземпляры создаются при импорте: конфиг читается на старте, и ошибка видна там же.
"""

from __future__ import annotations

from ai_geo_audit.config._base import Settings
from ai_geo_audit.config.domains import (
    AccessSettings,
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

DOMAINS: tuple[type[Settings], ...] = (
    AppSettings,
    StorageSettings,
    AccessSettings,
    KeySettings,
    LogSettings,
)


def env_names() -> list[str]:
    """Имена всех переменных окружения, которые читает конфиг."""
    return sorted(str(f.validation_alias) for d in DOMAINS for f in d.model_fields.values())


__all__ = [
    "DOMAINS",
    "AccessSettings",
    "AppSettings",
    "KeySettings",
    "LogSettings",
    "Settings",
    "StorageSettings",
    "access",
    "app",
    "env_names",
    "keys",
    "logs",
    "storage",
]
