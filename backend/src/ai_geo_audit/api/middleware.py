"""Слой входа — чистый ASGI; не-`http` области (`lifespan`) идут насквозь.

Порядок снаружи внутрь: перехват падения; номер запроса; секреты запроса; предел запросов в
работе; ворота; предел тела; строка доступа — после ответа и после перехвата. Номер и секреты слой
кладёт в область запроса: оттуда их берут перехват, обработчики отказов и журнал. Присланный
`X-Request-ID` принимается, только если он один и весь по образцу, иначе — свой (A46). Секреты
запроса — пропуск по форме и пароль сайта из `Authorization: Basic`: записи этого запроса их не
несут, и запись перехвата тоже (A48). Сверх `IN_FLIGHT_LIMIT` запросов в работе — 503 `busy` до
ворот, и здоровью тоже: оно открывает своё соединение к базе на каждую проверку (A54). Ворота стоят
до маршрутизации и до чтения тела: без годного пропуска — 403 `signed-out`, `receive` не
вызывается. Предел тела — после ворот, на любом пропущенном пути: объявленный размер больше
`BODY_LIMIT_BYTES` — 413 `too-large` до чтения, иначе чтение сверх предела кончается тем же
отказом (A53). Строка доступа — одна на запрос, и у отказов, и у 500: путь без строки запроса и с
процентами, адрес клиента — после слоя прокси uvicorn (A47).
"""

from __future__ import annotations

import base64
import logging
import re
import time
import uuid
from collections.abc import Iterator, Mapping, MutableMapping
from contextlib import contextmanager
from typing import Any
from urllib.parse import quote

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ai_geo_audit.api import limits
from ai_geo_audit.api.access import TOKEN_HEADER, VisitorCheck
from ai_geo_audit.api.contract import Refusal, refusal_response
from ai_geo_audit.logs.setup import TRUSTED, request_context, request_secrets

log = logging.getLogger("ai_geo_audit.api")
access_log = logging.getLogger("ai_geo_audit.api.access")
REQUEST_ID_HEADER = "X-Request-ID"
#: Присланный номер запроса: латиница, цифры, `.`, `_`, `-`, от 1 до 64 знаков.
SENT_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")
#: Пропуск по форме: три части через точку, первая — с `eyJ`. Значение выбирает клиент, поэтому
#: секрет — только по форме: `X-Access-Token: /api/nope` вычистил бы путь своего запроса (A48).
#: Поля строки доступа слой берёт из области запроса: секреты запроса их не трогают.
ACCESS_FIELDS = ("method", "path", "status", "duration_ms", "client")
PASS_FORM = re.compile(r"eyJ[\w-]*\.[\w-]+\.[\w-]+", re.ASCII)
#: Объявленный размер тела — только цифрами ASCII и байтами: строка latin-1 «²» прошла бы
#: `isdigit()` и уронила бы `int()`. Длиннее 19 цифр — не размер.
DECLARED_SIZE = re.compile(rb"[0-9]{1,19}")


def request_id_for(scope: Scope) -> str:
    """Присланный номер — у единственного заголовка и по образцу целиком; иначе свой (A46)."""
    sent = [value.decode("latin-1") for name, value in scope["headers"] if name == b"x-request-id"]
    if len(sent) == 1 and SENT_REQUEST_ID.fullmatch(sent[0]):
        return sent[0]
    return uuid.uuid4().hex


def secrets_for(scope: Scope) -> tuple[str, ...]:
    """Секреты запроса: пропуск по форме и из `Authorization: Basic` — строка после схемы и пароль.
    Значения — строки latin-1, как у Starlette: байты не UTF-8 разбор не роняют (A48)."""
    found: list[str] = []
    for name, raw in scope["headers"]:
        value = raw.decode("latin-1")
        if name == b"x-access-token" and PASS_FORM.fullmatch(value):
            found.append(value)
        elif name == b"authorization":
            found.extend(_basic_secrets(value))
    return tuple(found)


def _basic_secrets(value: str) -> list[str]:
    """Схема `Basic` — без учёта регистра; пароль — всё после первого двоеточия. После другой
    схемы секрет — только пропуск по форме."""
    scheme, _, encoded = value.strip().partition(" ")
    encoded = encoded.strip()
    if scheme.lower() != "basic" or not encoded:
        return [encoded] if PASS_FORM.fullmatch(encoded) else []
    try:
        pair = base64.b64decode(encoded, validate=True).decode("utf-8")
    except ValueError:  # `binascii.Error` и `UnicodeDecodeError` — оба `ValueError`: мусор не 500
        return [encoded]
    _, colon, password = pair.partition(":")
    return [encoded, password] if colon else [encoded]


def declared_size(scope: Scope) -> int:
    """Наибольший размер тела, объявленный любым `content-length` из цифр; мусор — не размер."""
    sizes = [
        int(value)
        for name, value in scope["headers"]
        if name == b"content-length" and DECLARED_SIZE.fullmatch(value)
    ]
    return max(sizes, default=0)


@contextmanager
def _journal(state: MutableMapping[str, Any]) -> Iterator[None]:
    """Номер и секреты запроса для записей журнала; шаг не успел — номер свой, секретов нет."""
    number = state.setdefault("request_id", uuid.uuid4().hex)
    with request_context(number), request_secrets(*state.get("secrets", ())):
        yield


class EntryLayer:
    """Перехват падения, номер и секреты запроса, ворота входа и строка доступа — одним слоем."""

    def __init__(
        self, app: ASGIApp, *, visitor_for: VisitorCheck, open_paths: Mapping[str, str]
    ) -> None:
        self.app = app
        self.visitor_for = visitor_for
        self.open_paths = frozenset(open_paths)
        #: Запросы в работе. Один процесс, один цикл событий: между проверкой и прибавкой нет
        #: `await`, гонки нет.
        self.in_flight = 0

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        state = scope.setdefault("state", {})
        began = time.monotonic()
        status: int | None = None

        async def send_numbered(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = state["request_id"]
            await send(message)

        try:
            state["request_id"] = request_id_for(scope)
            state["secrets"] = secrets_for(scope)
            with _journal(state):
                await self._limited(scope, receive, send_numbered)
        except Exception:
            # Перехват снаружи номера и секретов: их шаги — под ним, а запись ERROR заново входит
            # в их контексты из области запроса.
            with _journal(state):
                log.exception("запрос упал")
            if status is None:
                refusal = refusal_response("internal", request_id=state["request_id"])
                await refusal(scope, receive, send_numbered)
        finally:
            # После перехвата: у 500 строка несёт его код, у отменённого запроса кода нет.
            client = scope.get("client") or ("", 0)
            with _journal(state):
                access_log.info(
                    "запрос",
                    extra={
                        "method": scope["method"],
                        "path": quote(scope["path"]),
                        "status": status,
                        "duration_ms": int((time.monotonic() - began) * 1000),
                        "client": client[0],
                        TRUSTED: ACCESS_FIELDS,
                    },
                )

    async def _limited(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Сверх предела — `busy` сразу, приложение не зовётся; место освобождает любой исход."""
        limit = limits.IN_FLIGHT_LIMIT
        if self.in_flight >= limit:
            log.warning("сервис занят", extra={"in_flight": self.in_flight, "limit": limit})
            await refusal_response("busy")(scope, receive, send)
            return
        self.in_flight += 1
        try:
            await self._gate(scope, receive, send)
        finally:
            self.in_flight -= 1

    async def _gate(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["path"] not in self.open_paths:
            token = Headers(scope=scope).get(TOKEN_HEADER)
            visitor = await self.visitor_for(token) if token else None
            if visitor is None:
                await refusal_response("signed-out")(scope, receive, send)
                return
            scope["state"]["visitor"] = visitor
        await self._bounded(scope, receive, send)

    async def _bounded(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Объявлено больше предела — отказ до чтения; иначе байты считаются в `receive`, и сверх
        предела чтение тела кончается отказом: FastAPI пропускает его как есть (проба 2)."""
        limit = limits.BODY_LIMIT_BYTES
        declared = declared_size(scope)
        if declared > limit:
            log.warning(
                "тело запроса больше предела",
                extra={"limit_bytes": limit, "declared_bytes": declared},
            )
            await refusal_response("too-large")(scope, receive, send)
            return
        read = 0

        async def receive_counted() -> Message:
            nonlocal read
            message = await receive()
            read += len(message.get("body", b""))
            if read > limit:
                log.warning(
                    "тело запроса больше предела", extra={"limit_bytes": limit, "read_bytes": read}
                )
                raise Refusal("too-large")
            return message

        await self.app(scope, receive_counted, send)
