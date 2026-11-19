"""Договор отказов API: один вид — одна форма `{"detail", "kind", "retry"}`, текст — для
человека, признак доступа — в заголовке `X-Access-Refusal`. Образцы — `contracts/api-refusals.json`.

Голый `HTTPException` библиотеки становится видом договора: 404 — `not-found`, 405 —
`method-not-allowed` с `Allow`, прочий 4xx — `invalid` со своим кодом и пустым `errors`, 5xx —
`internal`; текста исключения в ответе нет. В 422 наружу — только `loc` и `type` каждой ошибки.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

REFUSAL_HEADER = "X-Access-Refusal"


@dataclass(frozen=True, slots=True)
class Kind:
    status: int
    retry: bool
    detail: str
    #: Отказ доступа: вид уходит и в заголовке — экран читает признак, а не текст.
    access: bool = False


KINDS: dict[str, Kind] = {
    "signed-out": Kind(403, False, "Нужен вход — войдите заново", access=True),
    "not-found": Kind(404, False, "Такого адреса нет"),
    "method-not-allowed": Kind(405, False, "Это действие здесь не поддерживается"),
    "invalid": Kind(422, False, "Запрос заполнен неверно — проверьте данные"),
    "internal": Kind(500, False, "Сбой на нашей стороне. Номер запроса: {request_id}"),
    "unavailable": Kind(503, True, "Сервис временно недоступен — повторите позже"),
}


class RequestModel(BaseModel):
    """База схем запросов: неизвестное поле — отказ, а не молчаливый пропуск."""

    model_config = ConfigDict(extra="forbid")


class Refusal(HTTPException):
    """Отказ договора: вид из таблицы; код — из вида."""

    def __init__(self, kind: str, *, headers: dict[str, str] | None = None) -> None:
        self.kind = kind
        super().__init__(KINDS[kind].status, headers=headers)


def refusal_response(
    kind: str,
    *,
    request_id: str = "",
    status: int | None = None,
    headers: dict[str, str] | None = None,
    errors: list[dict[str, object]] | None = None,
) -> JSONResponse:
    """Ответ отказа по виду; у `invalid` — ещё `errors`."""
    spec = KINDS[kind]
    body: dict[str, object] = {
        "detail": spec.detail.format(request_id=request_id),
        "kind": kind,
        "retry": spec.retry,
    }
    if errors is not None:
        body["errors"] = errors
    head = dict(headers or {})
    if spec.access:
        head[REFUSAL_HEADER] = kind
    return JSONResponse(body, status_code=status or spec.status, headers=head)


def request_id_of(request: Request) -> str:
    """Номер запроса, который слой входа положил в область запроса."""
    return str(request.scope.get("state", {}).get("request_id", ""))


async def on_http_exception(request: Request, exc: Exception) -> JSONResponse:
    """`HTTPException` — в вид договора; текст исключения наружу не идёт."""
    assert isinstance(exc, HTTPException)
    if isinstance(exc, Refusal):
        return refusal_response(exc.kind, headers=exc.headers)
    if exc.status_code == 404:
        return refusal_response("not-found")
    if exc.status_code == 405:
        return refusal_response("method-not-allowed", headers=exc.headers)
    if exc.status_code >= 500:
        return refusal_response("internal", request_id=request_id_of(request))
    return refusal_response("invalid", status=exc.status_code, errors=[])


async def on_validation_error(request: Request, exc: Exception) -> JSONResponse:
    """422 `invalid`: у каждой ошибки — только `loc` и `type`, без текста и введённого."""
    assert isinstance(exc, RequestValidationError)
    errors = [{"loc": list(error["loc"]), "type": error["type"]} for error in exc.errors()]
    return refusal_response("invalid", errors=errors)
