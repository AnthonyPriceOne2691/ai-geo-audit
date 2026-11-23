"""Проверка конфига на старте: процесс без нужной настройки падает сразу и называет все
разом — а не роняет первый аудит, потратив оплаченные запросы. В `dev` и `test` молчит;
в `prod` обязательное задаётся явно, заглушка из `.env.example` не годится. Ключи внешних
сервисов на старте не нужны: они в базе и меняются с экрана ключей.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlsplit

from pydantic import SecretStr
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from ai_geo_audit import config

#: Значения-заглушки в `.env.example` начинаются с этой метки.
PLACEHOLDER = "CHANGE_ME"
#: Секрет подписи короче 32 байт перебирается; `openssl rand -hex 32` даёт 64 знака.
MIN_SECRET_BYTES = 32
#: Пароль базы в бою — не короче, по знакам после раскодирования процентной записи (A21).
MIN_DB_PASSWORD = 12
#: Причина, когда драйвер и `urlsplit` читают адрес базы по-разному: без адреса и его кусков.
DB_URL_UNENCODED_CHARS = "в пароле незакодированные знаки — закодируйте @ / ? # как %40 %2F %3F %23"

Process = Literal["api", "worker", "migrate"]

#: Миграциям не нужен секрет входа, воркеру — тоже; адреса прокси нужны только api.
_NEEDS: dict[Process, frozenset[str]] = {
    "api": frozenset(
        {"DATABASE_URL", "REDIS_URL", "JWT_SECRET", "KEYS_MASTER_KEY_FILE", "API_TRUSTED_PROXIES"}
    ),
    "worker": frozenset({"DATABASE_URL", "REDIS_URL", "KEYS_MASTER_KEY_FILE"}),
    "migrate": frozenset({"DATABASE_URL"}),
}


def required(process: Process) -> frozenset[str]:
    """Имена, которые проверка старта требует от процесса; по ним правила стека раздают
    переменные сервисам compose (`ai_geo_audit.ops.stack_rules`)."""
    return _NEEDS[process]


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


def _decoded(part: str | None) -> str | None:
    return None if part is None else unquote(part)


def _read_by_both(url: str) -> tuple[object, ...] | None:
    """Имя, пароль, хост, порт и база — как их прочтут драйвер и `urlsplit`; разошлись — None.
    Хост — без регистра, имя, пароль и база — после раскодирования: иначе годный адрес с
    заглавными в хосте или `%40` в имени получил бы ложный отказ."""
    try:
        driver, plain = make_url(url), urlsplit(url)
        port = plain.port
    except (ArgumentError, ValueError):
        return None
    by_driver = (driver.username, driver.password, (driver.host or "").lower(), driver.port)
    by_split = (_decoded(plain.username), _decoded(plain.password), plain.hostname or "", port)
    database = _decoded(plain.path.removeprefix("/")) or None
    if (*by_driver, driver.database) != (*by_split, database):
        return None
    return (*by_driver, driver.database)


def _database(storage: config.StorageSettings) -> Problem | None:
    """Адрес базы в бою: задан явно, без заглушки, читается одинаково, пароль не короче 12."""
    if problem := _explicit("DATABASE_URL", storage, "database_url"):
        return problem
    parts = _read_by_both(storage.database_url.get_secret_value())
    if parts is None:
        return Problem("DATABASE_URL", DB_URL_UNENCODED_CHARS)
    password = parts[1] if isinstance(parts[1], str) else ""
    if len(password) < MIN_DB_PASSWORD:
        return Problem("DATABASE_URL", f"пароль базы короче {MIN_DB_PASSWORD} знаков")
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
    api: config.ApiSettings | None = None,
) -> list[Problem]:
    """Все негодные настройки процесса в боевом режиме; вне `prod` — пустой список."""
    if (app or config.app).env != "prod":
        return []
    storage = storage or config.storage
    secret = (access or config.access).jwt_secret.get_secret_value()
    checks = {
        "DATABASE_URL": _database(storage),
        "REDIS_URL": _explicit("REDIS_URL", storage, "redis_url"),
        "JWT_SECRET": _secret("JWT_SECRET", secret, "подпись пропусков: openssl rand -hex 32"),
        "KEYS_MASTER_KEY_FILE": _master_key((keys or config.keys).master_key_file),
        "API_TRUSTED_PROXIES": _explicit(
            "API_TRUSTED_PROXIES", api or config.api, "trusted_proxies_text"
        ),
    }
    return [p for name, p in checks.items() if name in required(process) and p is not None]


def check_startup(process: Process, found: list[Problem] | None = None) -> None:
    """Упасть до работы, если в боевом режиме чего-то не хватает; назвать всё разом."""
    found = problems(process) if found is None else found
    if found:
        lines = "\n".join(f"  {p.env_name} — {p.why}" for p in found)
        raise ConfigError(f"Процесс «{process}» не стартует в боевом режиме:\n{lines}")


# Проба A288 «Скорости CI»: метка области гейта мутаций, в main не идёт.
