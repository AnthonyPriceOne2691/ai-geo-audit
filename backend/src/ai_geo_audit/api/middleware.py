"""Слой входа — чистый ASGI; не-`http` области (`lifespan`) идут насквозь.

Порядок: перехват падения — снаружи всего; номер запроса; ворота. Номер слой кладёт в область
запроса: оттуда его берут перехват, обработчики отказов и журнал. Ворота стоят до маршрутизации
и до чтения тела: без годного пропуска — 403 `signed-out`, `receive` не вызывается.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ai_geo_audit.api.access import TOKEN_HEADER, VisitorCheck
from ai_geo_audit.api.contract import refusal_response
from ai_geo_audit.logs.setup import request_context

log = logging.getLogger("ai_geo_audit.api")
REQUEST_ID_HEADER = "X-Request-ID"


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
        request_id = uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        started = False

        async def send_numbered(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        with request_context(request_id):
            try:
                await self._gate(scope, receive, send_numbered)
            except Exception:
                log.exception("запрос упал")
                if not started:
                    refusal = refusal_response("internal", request_id=request_id)
                    await refusal(scope, receive, send_numbered)

    async def _gate(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["path"] not in self.open_paths:
            token = Headers(scope=scope).get(TOKEN_HEADER)
            visitor = await self.visitor_for(token) if token else None
            if visitor is None:
                await refusal_response("signed-out")(scope, receive, send)
                return
            scope["state"]["visitor"] = visitor
        await self.app(scope, receive, send)
