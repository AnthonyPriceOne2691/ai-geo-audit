"""Логирование: поля из `extra` и id запроса и аудита доживают до вывода, секреты — нет.

Время — всегда UTC (`+0000`): у машины разработчика и сервера пояса разные. Секреты
вычищаются из готовой записи; httpx и httpcore — на WARNING: на INFO они печатают адрес
целиком, а в адресе бывает токен.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from urllib.parse import urlsplit

from pydantic import SecretStr

from ai_geo_audit import config

_request_id: ContextVar[str] = ContextVar("request_id", default="")
_audit_id: ContextVar[str] = ContextVar("audit_id", default="")

#: Значения, которых не должно быть в выводе. Короткие не берём: маска съела бы слова.
_secrets: set[str] = set()
_MIN_SECRET_LEN = 6
MASK = "***"

#: Поля, которые `logging` кладёт в запись сам; всё прочее пришло из `extra`.
_STANDARD = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message"}
_NOISY_LOGGERS = ("httpx", "httpcore")


def register_secret(value: str) -> None:
    """Отметить значение, которое не должно попасть в журнал."""
    if len(value) >= _MIN_SECRET_LEN:
        _secrets.add(value)


def register_config_secrets(*domains: config.Settings) -> None:
    """Отметить секретные настройки доменов; у адреса базы — ещё и пароль отдельно."""
    for settings in domains:
        for value in settings.__dict__.values():
            if isinstance(value, SecretStr):
                secret = value.get_secret_value()
                register_secret(secret)
                if "://" in secret and (password := urlsplit(secret).password):
                    register_secret(password)


def redact(text: str) -> str:
    """Заменить известные секреты на маску; длинные — первыми."""
    for secret in sorted(_secrets, key=len, reverse=True):
        text = text.replace(secret, MASK)
    return text


@contextmanager
def _tagged(var: ContextVar[str], value: str | int) -> Iterator[None]:
    token = var.set(str(value))
    try:
        yield
    finally:
        var.reset(token)


def request_context(request_id: str) -> AbstractContextManager[None]:
    """Пометить все записи внутри блока идентификатором запроса."""
    return _tagged(_request_id, request_id)


def audit_context(audit_id: str | int) -> AbstractContextManager[None]:
    """Пометить все записи внутри блока идентификатором аудита."""
    return _tagged(_audit_id, audit_id)


class ContextFilter(logging.Filter):
    """Ставит идентификаторы каждой записи. Висит на обработчике: так и на чужих записях."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get()
        record.audit_id = _audit_id.get()
        return True


def _utc(record: logging.LogRecord) -> str:
    moment = datetime.fromtimestamp(record.created, tz=UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(record.msecs):03d}+0000"


def _extra(record: logging.LogRecord) -> dict[str, object]:
    fields = record.__dict__.items()
    return {k: v for k, v in fields if k not in _STANDARD and not k.startswith("_")}


def _scrub(value: object) -> object:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {key: _scrub(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_scrub(item) for item in value]
    return value


class JsonFormatter(logging.Formatter):
    """Одна запись — одна строка JSON; поля из `extra` остаются полями."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": _utc(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            **_extra(record),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # `default=str`: падение логгера на несериализуемом поле — худший исход. Второй
        # проход `redact` — по строке: секрет мог приехать в объекте, ставшем строкой здесь.
        return redact(json.dumps(_scrub(payload), ensure_ascii=False, default=str))


class TextFormatter(logging.Formatter):
    """Строка для человека; поля из `extra` — в хвосте."""

    def format(self, record: logging.LogRecord) -> str:
        line = f"{_utc(record)} {record.levelname} {record.name} {record.getMessage()}"
        fields = {k: v for k, v in _extra(record).items() if v not in ("", None)}
        if fields:
            line += " | " + " ".join(f"{k}={v}" for k, v in sorted(fields.items()))
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return redact(line)


def setup_logging(level: str | None = None, fmt: str | None = None) -> None:
    """Собрать корневой логгер; повторный вызов заменяет обработчик, а не добавляет второй."""
    register_config_secrets(config.storage, config.access, config.keys)
    handler = logging.StreamHandler(sys.stderr)
    chosen = (fmt or config.logs.format).lower()
    handler.setFormatter(JsonFormatter() if chosen == "json" else TextFormatter())
    handler.addFilter(ContextFilter())
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel((level or config.logs.level).upper())
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
