"""Слой входа — чистый ASGI; не-`http` области (`lifespan`) идут насквозь.

Порядок: перехват падения — снаружи всего; номер запроса; ворота; строка доступа — после ответа.
Номер слой кладёт в область запроса: оттуда его берут перехват, обработчики отказов и журнал.
Присланный `X-Request-ID` принимается, только если он один и весь по образцу, иначе — свой
(A46). Ворота стоят до маршрутизации и до чтения тела: без годного пропуска — 403 `signed-out`,
`receive` не вызывается. Строка доступа — одна на запрос, и у отказа ворот тоже: путь без строки
запроса и с процентами, адрес клиента — после слоя прокси uvicorn (A47).
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import Mapping
from urllib.parse import quote

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ai_geo_audit.api.access import TOKEN_HEADER, VisitorCheck
from ai_geo_audit.api.contract import refusal_response
from ai_geo_audit.logs.setup import request_context

log = logging.getLogger("ai_geo_audit.api")
access_log = logging.getLogger("ai_geo_audit.api.access")
REQUEST_ID_HEADER = "X-Request-ID"
#: Присланный номер запроса: латиница, цифры, `.`, `_`, `-`, от 1 до 64 знаков.
SENT_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")


def request_id_for(scope: Scope) -> str:
    """Присланный номер — у единственного заголовка и по образцу целиком; иначе свой (A46)."""
    sent: list[str] = [
        value.decode("latin-1") for name, value in scope["headers"] if name == b"x-request-id"
    ]
    if len(sent) == 1 and SENT_REQUEST_ID.fullmatch(sent[0]):
        return sent[0]
    return uuid.uuid4().hex


class EntryLayer:
    """Перехват падения, номер запроса и ворота входа — одним слоем."""

    def __init__(
        self, app: ASGIApp, *, visitor_for: VisitorCheck, open_paths: Mapping[str, str]
    ) -> None:
        self.app = app
        self.visitor_for = visitor_for
        self.open_paths = frozenset(open_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = request_id_for(scope)
        scope.setdefault("state", {})["request_id"] = request_id
        began = time.monotonic()
        status: int | None = None

        async def send_numbered(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        with request_context(request_id):
            try:
                await self._gate(scope, receive, send_numbered)
            except Exception:
                log.exception("запрос упал")
                if status is None:
                    refusal = refusal_response("internal", request_id=request_id)
                    await refusal(scope, receive, send_numbered)
            finally:
                client = scope.get("client") or ("", 0)
                access_log.info(
                    "запрос",
                    extra={
                        "method": scope["method"],
                        "path": quote(scope["path"]),
                        "status": status,
                        "duration_ms": int((time.monotonic() - began) * 1000),
                        "client": client[0],
                    },
                )

    async def _gate(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["path"] not in self.open_paths:
            token = Headers(scope=scope).get(TOKEN_HEADER)
            visitor = await self.visitor_for(token) if token else None
            if visitor is None:
                await refusal_response("signed-out")(scope, receive, send)
                return
            scope["state"]["visitor"] = visitor
        await self.app(scope, receive, send)
