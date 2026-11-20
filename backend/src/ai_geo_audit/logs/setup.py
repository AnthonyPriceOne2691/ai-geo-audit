"""Логирование: поля из `extra` и id запроса и аудита доживают до вывода, секреты — нет.

Время — всегда UTC (`+0000`): у машины разработчика и сервера пояса разные. Секреты настроек
вычищаются из готовой записи целиком: их клиент не выбирает. Секреты запроса — пропуск и пароль
сайта, в переменной контекста — выбирает клиент заголовком, поэтому они вычищаются только из
текста, который написал код: сообщения, трассировки и полей `extra`. Поля, которые слой взял из
области запроса и из себя, и форма записи от них не зависят (A48). Поле `extra` с именем базового
поля записи выходит с приставкой `extra_` и его не перебивает (A56). Каждое поле чистится до сборки
строки — JSON остаётся JSON. Запись, которая не собралась, выходит записью-заменой без сырого
текста; предупреждения и необработанные исключения процесса — записями через вычистку, крючки
ставит точка входа процесса (`guard_process`). httpx и httpcore — на WARNING: на INFO они печатают
адрес целиком, а в адресе бывает токен.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import sys
import threading
from collections.abc import Iterable, Iterator
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from types import TracebackType
from typing import TextIO, override
from urllib.parse import unquote, urlsplit

from pydantic import SecretStr

from ai_geo_audit import config

_request_id: ContextVar[str] = ContextVar("request_id", default="")
_audit_id: ContextVar[str] = ContextVar("audit_id", default="")

#: Значения, которых не должно быть в выводе. Короткие не берём: маска съела бы слова.
_secrets: set[str] = set()
#: Секреты запроса: живут в своём запросе. Общий набор рос бы на каждый новый пропуск, а
#: `redact` обходит его на каждой записи.
_request_secrets: ContextVar[tuple[str, ...]] = ContextVar("request_secrets", default=())
_MIN_SECRET_LEN = 6
MASK = "***"
#: Значения строки, которые Postgres сам кладёт в DETAIL ошибки: `Key (email)=(…)`,
#: `Failing row contains (…)`, исключающее ограничение. Имена колонок остаются — по ним ищут
#: причину. Значение бывает с переводом строки и с «).» внутри: конец — «).» перед концом строки.
_PG_VALUES = (
    re.compile(
        r"(Key \([^)]*\)=\().*?(\) (?:already exists|is not present|is still ref"
        r"|conflicts with existing key))",
        re.DOTALL,
    ),
    re.compile(r"(conflicts with existing key \([^)]*\)=\().*?(\)\.)(?=\n|$)", re.DOTALL),
    re.compile(r"(Failing row contains \().*?(\)\.)(?=\n|$)", re.DOTALL),
)

#: Поля, которые `logging` кладёт в запись сам; всё прочее пришло из `extra`.
_STANDARD = frozenset(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message"}
_NOISY_LOGGERS = ("httpx", "httpcore")
#: Поля записи, которые в вывод не идут: `color_message` — копия сообщения uvicorn с цветом,
#: управляющими знаками терминала.
_DROPPED = frozenset({"color_message"})
#: Базовые поля вывода: одноимённое поле `extra` выходит как `extra_<имя>` (A56).
_RESERVED = frozenset({"ts", "level", "logger", "exc"})
#: Атрибут записи — поля, которые код взял из области запроса и из себя, а не от клиента (строка
#: доступа). Секреты запроса по ним не идут: клиент заголовком не выберет, что в них потерять.
TRUSTED = "_trusted"
#: Поля, которые ставит фильтр журнала поверх присланного: их клиент тоже не выбирает.
_OWN = frozenset({"request_id", "audit_id"})
#: Сообщение записи-замены: исходная запись не собралась, её текста и аргументов в выводе нет.
UNBUILT = "запись журнала не собралась"
_process_log = logging.getLogger("ai_geo_audit.process")


def register_secret(value: str) -> None:
    """Отметить значение, которое не должно попасть в журнал."""
    if len(value) >= _MIN_SECRET_LEN:
        _secrets.add(value)


def register_config_secrets(*domains: config.Settings) -> None:
    """Отметить секретные настройки доменов; у адресов — ещё и пароль отдельно, в записи и
    раскодированный: `p%40ss` в адресе — это `p@ss` в тексте ошибки."""
    for settings in domains:
        for value in settings.__dict__.values():
            if isinstance(value, SecretStr):
                secret = value.get_secret_value()
                register_secret(secret)
                if "://" in secret and (password := urlsplit(secret).password):
                    register_secret(password)
                    register_secret(unquote(password))


def _mask(text: str, secrets: Iterable[str]) -> str:
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, MASK)
    return text


def redact(text: str) -> str:
    """Заменить на маску секреты настроек (длинные — первыми) и значения строк из ошибок
    Postgres — по всему тексту: их клиент не выбирает."""
    text = _mask(text, _secrets)
    for pattern in _PG_VALUES:
        text = pattern.sub(rf"\g<1>{MASK}\g<2>", text)
    return text


def _written(text: str) -> str:
    """Текст, который написал код: секреты настроек, затем секреты своего запроса. Настройки —
    первыми: секрет запроса, пересекшийся с секретом настроек, не откроет его кусок."""
    return _mask(redact(text), _request_secrets.get())


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


@contextmanager
def request_secrets(*values: str) -> Iterator[None]:
    """Секреты запроса на время блока; на выходе — прежний набор. Короткие не берутся, как и в
    общем наборе. Набор ставится и пустым: так чужой набор, унаследованный задачей, не доживает
    до записей этого запроса (проба 8)."""
    token = _request_secrets.set(tuple(v for v in values if len(v) >= _MIN_SECRET_LEN))
    try:
        yield
    finally:
        _request_secrets.reset(token)


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
    dropped = _STANDARD | _DROPPED
    return {
        f"extra_{k}" if k in _RESERVED else k: v
        for k, v in fields
        if k not in dropped and not k.startswith("_")
    }


def _clean(text: str, written: bool) -> str:
    return _written(text) if written else redact(text)


def _scrub(value: object, written: bool) -> object:
    """Значение поля: у текста кода — и секреты запроса; ключ словаря чистится как значение и
    становится строкой; объект, который станет строкой в выводе, чистится своей строкой."""
    if value is None or isinstance(value, bool | int | float):
        return value
    if isinstance(value, dict):
        return {_clean(str(key), written): _scrub(item, written) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_scrub(item, written) for item in value]
    return _clean(value if isinstance(value, str) else str(value), written)


def _trusted(record: logging.LogRecord) -> frozenset[str]:
    return _OWN | frozenset(getattr(record, TRUSTED, ()))


def _unbuilt(record: logging.LogRecord, error: BaseException | None) -> dict[str, str]:
    """Поля записи-замены: логгер, уровень и место вызова исходной записи и тип ошибки сборки —
    без сообщения и аргументов: в них бывают секреты."""
    return {
        "ts": _utc(record),
        "level": record.levelname,
        "logger": redact(record.name),
        "message": UNBUILT,
        "error": type(error).__name__ if error else "",
        "source": f"{record.filename}:{record.lineno}",
        "request_id": str(getattr(record, "request_id", "")),
        "audit_id": str(getattr(record, "audit_id", "")),
    }


class JsonFormatter(logging.Formatter):
    """Одна запись — одна строка JSON; поля из `extra` остаются полями."""

    def unbuilt(self, record: logging.LogRecord, error: BaseException | None) -> str:
        return json.dumps(_unbuilt(record, error), ensure_ascii=False)

    def format(self, record: logging.LogRecord) -> str:
        trusted = _trusted(record)
        payload: dict[str, object] = {
            "ts": _utc(record),
            "level": record.levelname,
            "logger": redact(record.name),
            "message": _written(record.getMessage()),
            **{k: _scrub(v, k not in trusted) for k, v in _extra(record).items()},
        }
        if record.exc_info:
            payload["exc"] = _written(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    """Строка для человека; поля из `extra` — в хвосте."""

    def unbuilt(self, record: logging.LogRecord, error: BaseException | None) -> str:
        fields = _unbuilt(record, error)
        head = " ".join(fields.pop(name) for name in ("ts", "level", "logger", "message"))
        return head + " | " + " ".join(f"{k}={v}" for k, v in sorted(fields.items()) if v)

    def format(self, record: logging.LogRecord) -> str:
        trusted = _trusted(record)
        name, message = redact(record.name), _written(record.getMessage())
        line = f"{_utc(record)} {record.levelname} {name} {message}"
        fields = {k: v for k, v in _extra(record).items() if v not in ("", None)}
        if fields:
            line += " | " + " ".join(
                f"{k}={_clean(str(v), k not in trusted)}" for k, v in sorted(fields.items())
            )
        if record.exc_info:
            line += "\n" + _written(self.formatException(record.exc_info))
        return line


class _Handler(logging.StreamHandler[TextIO]):
    """Обработчик журнала: запись, которая не собралась (ошибка формата, поле, что не печатается),
    или сбой записи в поток — запись-замена вместо сырого сообщения и аргументов, которые
    стандартный `handleError` печатает мимо форматтера. Замена — в `stderr`, как у стандартного."""

    def __init__(self, stream: TextIO, formatter: JsonFormatter | TextFormatter) -> None:
        super().__init__(stream)
        self.setFormatter(formatter)
        self.ours = formatter

    @override
    def handleError(self, record: logging.LogRecord) -> None:
        with contextlib.suppress(Exception):
            sys.stderr.write(self.ours.unbuilt(record, sys.exc_info()[1]) + self.terminator)


def setup_logging(level: str | None = None, fmt: str | None = None) -> None:
    """Собрать корневой логгер; повторный вызов заменяет обработчик, а не добавляет второй."""
    register_config_secrets(config.storage, config.access, config.keys)
    chosen = (fmt or config.logs.format).lower()
    handler = _Handler(sys.stderr, JsonFormatter() if chosen == "json" else TextFormatter())
    handler.addFilter(ContextFilter())
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel((level or config.logs.level).upper())
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def guard_process() -> None:
    """Точка входа процесса: предупреждения Python, необработанные исключения и исключения, которые
    интерпретатор «игнорирует» (деструктор, сборщик, обратный вызов), — записями нашего формата
    через вычистку (A48, A55). Не в `setup_logging`: под pytest `captureWarnings` встал бы поверх
    его подмены `showwarning` на время теста, а крючки исключений — дело процесса."""
    logging.captureWarnings(True)
    sys.excepthook = _unhandled
    threading.excepthook = _unhandled_in_thread
    sys.unraisablehook = _unraisable


def _unhandled(
    kind: type[BaseException], error: BaseException, trace: TracebackType | None
) -> None:
    """Необработанное исключение главного потока — запись ERROR; код выхода не ноль даёт сам
    интерпретатор. Прерывание с клавиатуры — как обычно."""
    if issubclass(kind, KeyboardInterrupt):
        sys.__excepthook__(kind, error, trace)
        return
    _process_log.error("необработанное исключение", exc_info=(kind, error, trace))


def _unhandled_in_thread(args: threading.ExceptHookArgs) -> None:
    """Необработанное исключение потока — запись ERROR; `SystemExit` потока молчит, как обычно."""
    if args.exc_value is None or issubclass(args.exc_type, SystemExit):
        return
    name = args.thread.name if args.thread else ""
    trace = (args.exc_type, args.exc_value, args.exc_traceback)
    _process_log.error("необработанное исключение в потоке %s", name, exc_info=trace)


def _unraisable(unraisable: sys.UnraisableHookArgs) -> None:
    """Исключение, которое интерпретатор «игнорирует», — запись ERROR вместо «Exception ignored» и
    трассировки открытым текстом."""
    error = unraisable.exc_value
    trace = (unraisable.exc_type, error, unraisable.exc_traceback) if error is not None else None
    _process_log.error("исключение вне вызова в %r", unraisable.object, exc_info=trace)
