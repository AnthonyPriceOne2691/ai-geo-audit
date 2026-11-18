"""Проверка конфига на старте: процесс без нужной настройки падает сразу и называет все
разом — а не роняет первый аудит, потратив оплаченные запросы. В `dev` и `test` молчит;
в `prod` обязательное задаётся явно, заглушка из `.env.example` не годится. Ключи внешних
сервисов на старте не нужны: они в базе и меняются с экрана ключей.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import SecretStr

from ai_geo_audit import config

#: Значения-заглушки в `.env.example` начинаются с этой метки.
PLACEHOLDER = "CHANGE_ME"
#: Секрет подписи короче 32 байт перебирается; `openssl rand -hex 32` даёт 64 знака.
MIN_SECRET_BYTES = 32

Process = Literal["api", "worker", "migrate"]

#: Миграциям не нужен секрет входа, воркеру — тоже.
_NEEDS: dict[Process, frozenset[str]] = {
    "api": frozenset({"DATABASE_URL", "REDIS_URL", "JWT_SECRET", "KEYS_MASTER_KEY_FILE"}),
    "worker": frozenset({"DATABASE_URL", "REDIS_URL", "KEYS_MASTER_KEY_FILE"}),
    "migrate": frozenset({"DATABASE_URL"}),
}


class ConfigError(RuntimeError):
    """Обязательных настроек не хватает или они негодны."""


@dataclass(frozen=True, slots=True)
class Problem:
    env_name: str
    why: str


def _missing(env_name: str, value: str, purpose: str) -> Problem | None:
    if not value:
        return Problem(env_name, f"не задано — {purpose}")
    if value.startswith(PLACEHOLDER):
        return Problem(env_name, f"стоит заглушка из .env.example — {purpose}")
    return None


def _secret(env_name: str, value: str, purpose: str) -> Problem | None:
    if missing := _missing(env_name, value, purpose):
        return missing
    if len(value.encode("utf-8")) < MIN_SECRET_BYTES:
        return Problem(env_name, f"короче {MIN_SECRET_BYTES} байт — {purpose}")
    return None


def _explicit(env_name: str, settings: config.Settings, field: str) -> Problem | None:
    if field not in settings.model_fields_set:
        return Problem(env_name, "в бою задаётся явно: умолчание ведёт на машину разработчика")
    value = getattr(settings, field)
    text = value.get_secret_value() if isinstance(value, SecretStr) else str(value)
    if PLACEHOLDER in text:
        return Problem(env_name, "стоит заглушка из .env.example — подключение")
    return None


def _master_key(path: str) -> Problem | None:
    purpose = "мастер-ключ шифрования ключей сервисов"
    if missing := _missing("KEYS_MASTER_KEY_FILE", path, purpose):
        return missing
    if not Path(path).is_file():
        return Problem("KEYS_MASTER_KEY_FILE", f"файла нет: {path}")
    return None


def problems(
    process: Process,
    *,
    app: config.AppSettings | None = None,
    storage: config.StorageSettings | None = None,
    access: config.AccessSettings | None = None,
    keys: config.KeySettings | None = None,
) -> list[Problem]:
    """Все негодные настройки процесса в боевом режиме; вне `prod` — пустой список."""
    if (app or config.app).env != "prod":
        return []
    storage = storage or config.storage
    secret = (access or config.access).jwt_secret.get_secret_value()
    checks = {
        "DATABASE_URL": _explicit("DATABASE_URL", storage, "database_url"),
        "REDIS_URL": _explicit("REDIS_URL", storage, "redis_url"),
        "JWT_SECRET": _secret("JWT_SECRET", secret, "подпись пропусков: openssl rand -hex 32"),
        "KEYS_MASTER_KEY_FILE": _master_key((keys or config.keys).master_key_file),
    }
    return [p for name, p in checks.items() if name in _NEEDS[process] and p is not None]


def check_startup(process: Process, found: list[Problem] | None = None) -> None:
    """Упасть до работы, если в боевом режиме чего-то не хватает; назвать всё разом."""
    found = problems(process) if found is None else found
    if found:
        lines = "\n".join(f"  {p.env_name} — {p.why}" for p in found)
        raise ConfigError(f"Процесс «{process}» не стартует в боевом режиме:\n{lines}")
