"""Ворота входа и договор отказов, срез 2 (A42, A44, A45): путь вне списка открытых — только
с годным пропуском и до чтения тела; отказы — одной формой по файлу образцов."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator

import api_probes as probes
import pytest

from ai_geo_audit.api import access
from ai_geo_audit.logs import setup as log_setup

SIGNED_OUT = probes.sample("signed-out")


def refused_signed_out(response: object) -> list[str]:
    """Что не так с ответом «нужен вход»; пусто — ответ по образцу `signed-out`."""
    problems = []
    if response.status_code != 403:
        problems.append(f"код {response.status_code}")
    if response.headers.get("x-access-refusal") != "signed-out":
        problems.append("нет X-Access-Refusal: signed-out")
    if "www-authenticate" in response.headers:
        problems.append("есть WWW-Authenticate")
    if response.json() != SIGNED_OUT["body"]:
        problems.append(f"тело {response.json()}")
    return problems


WITHOUT_PASS = [
    ("GET", probes.CLOSED, {}),
    ("GET", probes.CLOSED, {"Authorization": "Bearer abc"}),
    ("GET", probes.CLOSED, {"Authorization": probes.BASIC}),
    ("GET", probes.CLOSED, {"X-Access-Token": "any-value-1234567890"}),
    ("GET", "/api/nope", {}),
    ("GET", "/", {}),
    ("GET", "/api/health/", {}),
    ("GET", "/api/health/x", {}),
]


@pytest.mark.parametrize(("method", "path", "headers"), WITHOUT_PASS)
async def test_a42_without_a_valid_pass_everything_is_signed_out(
    method: str, path: str, headers: dict[str, str]
) -> None:
    """A42: ворота с настоящей проверкой (до Ч1.8 она не признаёт ни одного пропуска): закрытая
    проба без заголовков, с `Authorization` `Bearer` и `Basic`, с чужим `X-Access-Token`;
    `/api/nope`; `/`; `/api/health/` и `/api/health/x` — всё 403 по образцу `signed-out`, без
    `WWW-Authenticate`. Есть адрес или нет — ответ один; путь сверяется точным равенством."""
    async with probes.client(probes.build(check=access.visitor_for)) as http:
        response = await http.request(method, path, headers=headers)
    assert refused_signed_out(response) == []


async def stream(sent: list[int]) -> AsyncIterator[bytes]:
    """Тело 5 МБ кусками по 64 КиБ; отданное — в счёт: клиент тянет его только из `receive`."""
    for _ in range(80):
        sent.append(65536)
        yield b"x" * 65536


@pytest.mark.parametrize("path", [probes.BODY, "/api/nope", "/"])
async def test_a42_gates_answer_before_reading_the_body(path: str) -> None:
    """A42: без пропуска `POST` пробы с телом, `/api/nope` и `/` — тело 5 МБ потоком без
    `Content-Length`: 403 `signed-out`, и не отдано ни байта — ворота отвечают до маршрутизации
    и до чтения тела (проба 5)."""
    sent: list[int] = []
    async with probes.client(probes.build()) as http:
        response = await http.post(path, content=stream(sent))
    assert refused_signed_out(response) == []
    assert sum(sent) == 0


async def test_a42_broken_json_without_a_pass_is_signed_out_not_invalid() -> None:
    """A42: без пропуска битый JSON на пробу с телом — 403 `signed-out`, а не 422."""
    async with probes.client(probes.build()) as http:
        response = await http.post(probes.BODY, content=b'{"name": "x", "limit": ')
    assert refused_signed_out(response) == []


async def test_a42_pass_is_read_only_from_its_header() -> None:
    """A42: подставная проверка признаёт тестовый пропуск. В `X-Access-Token` — закрытая проба
    отвечает именем вошедшего из состояния запроса, `/api/nope` — 404 `not-found`; тот же
    пропуск в `Authorization: Bearer` и в адресе — 403 `signed-out`."""
    token = {"X-Access-Token": probes.PASS}
    async with probes.client(probes.build()) as http:
        allowed = await http.get(probes.CLOSED, headers=token)
        missing = await http.get("/api/nope", headers=token)
        bearer = await http.get(probes.CLOSED, headers={"Authorization": f"Bearer {probes.PASS}"})
        in_url = await http.get(f"/api/nope?token={probes.PASS}")
    assert (allowed.status_code, allowed.json()) == (200, {"visitor": "probe-visitor"})
    assert (missing.status_code, missing.json()) == (404, probes.sample("not-found")["body"])
    assert refused_signed_out(bearer) == [] and refused_signed_out(in_url) == []


async def test_a42_failed_check_does_not_open_the_entry(capsys: pytest.CaptureFixture[str]) -> None:
    """A42: проверка пропуска падает `RuntimeError("check-down-7a1")` — 500 `internal` с номером
    запроса, проба не вызвана, метки в ответе нет; в журнале — ERROR с трассировкой и тем же
    номером."""
    log_setup.setup_logging(fmt="json")
    probes.CALLS.clear()
    async with probes.client(probes.build(check=probes.broken_check)) as http:
        response = await http.get(probes.CLOSED, headers={"X-Access-Token": probes.PASS})
    number = response.headers["x-request-id"]
    assert (response.status_code, response.json()["kind"]) == (500, "internal")
    assert number in response.json()["detail"] and "check-down-7a1" not in response.text
    assert probes.CALLS == []
    records = [json.loads(line) for line in capsys.readouterr().err.splitlines() if line]
    errors = [r for r in records if r["level"] == "ERROR"]
    assert [(r["request_id"], "check-down-7a1" in r.get("exc", "")) for r in errors] == [
        (number, True)
    ]


async def test_a42_mistake_in_the_open_list_does_not_open_a_closed_route() -> None:
    """A42: путь закрытой пробы по ошибке внесён в список открытых — без пропуска 403
    `signed-out` от зависимости закрытого роутера: вошедшего в состоянии запроса нет."""
    probes.CALLS.clear()
    mistaken = {**access.OPEN_PATHS, probes.CLOSED: "ошибка списка в тесте"}
    async with probes.client(probes.build(open_paths=mistaken)) as http:
        response = await http.get(probes.CLOSED)
    assert refused_signed_out(response) == []
    assert probes.CALLS == []


async def test_a42_lifespan_passes_through_the_layer() -> None:
    """A42: область `lifespan` — `startup`, затем `shutdown` прямо в приложение: оба
    `complete`; слой пути не спрашивает, ворота область не трогают."""
    messages = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])
    sent: list[str] = []

    async def receive() -> dict[str, str]:
        return next(messages)

    async def send(message: dict[str, str]) -> None:
        sent.append(message["type"])

    await probes.build()({"type": "lifespan", "asgi": {"version": "3.0"}}, receive, send)
    assert sent == ["lifespan.startup.complete", "lifespan.shutdown.complete"]


def test_a42_schema_names_only_the_pass_header() -> None:
    """A42: `app.openapi()` приложения с закрытой пробой — в `securitySchemes` ровно одна схема,
    `apiKey` в заголовке `X-Access-Token`; у закрытой пробы в `security` только она."""
    schema = probes.build().openapi()
    schemes = schema["components"]["securitySchemes"]
    assert list(schemes.values()) == [{"type": "apiKey", "in": "header", "name": "X-Access-Token"}]
    assert schema["paths"][probes.CLOSED]["get"]["security"] == [{next(iter(schemes)): []}]


async def test_a42_bare_401_of_the_library_is_signed_out() -> None:
    """A42: голый 401 библиотеки с `WWW-Authenticate` — ответ по образцу `signed-out`: 403, с
    признаком, без `WWW-Authenticate`. 401 сервис не отвечает: на него браузер стирает пароль
    сайта. Строже показанного — замечание проверяющего 2026-11-19T13:18Z."""
    async with probes.client(probes.build()) as http:
        response = await http.get(probes.UNAUTHORIZED, headers={"X-Access-Token": probes.PASS})
    assert refused_signed_out(response) == []


INVALID = probes.sample("invalid")["body"]


async def test_a44_validation_errors_carry_only_loc_and_type() -> None:
    """A44: проба с телом, тестовый пропуск, тело `{"limit": "secret-value-7f3a9c", "extra":
    "x"}` — 422 `invalid` по образцу; три записи `errors` с ключами ровно `loc` и `type`;
    `msg`, `input`, `ctx`, `url` нет, значения тела в байтах ответа нет."""
    payload = {"limit": "secret-value-7f3a9c", "extra": "x"}
    async with probes.client(probes.build()) as http:
        response = await http.post(
            probes.BODY, json=payload, headers={"X-Access-Token": probes.PASS}
        )
    body = response.json()
    assert response.status_code == 422
    assert {k: body[k] for k in ("detail", "kind", "retry")} == {
        k: INVALID[k] for k in ("detail", "kind", "retry")
    }
    assert body["errors"] == [
        {"loc": ["body", "name"], "type": "missing"},
        {"loc": ["body", "limit"], "type": "int_parsing"},
        {"loc": ["body", "extra"], "type": "extra_forbidden"},
    ]
    assert b"secret-value-7f3a9c" not in response.content


async def test_a44_broken_json_is_one_json_invalid_record() -> None:
    """A44: та же проба, битый JSON `{"name": "x", "limit": ` — 422 `invalid`, одна запись
    `json_invalid`, `loc` начинается с `"body"`; текста тела запроса в ответе нет."""
    async with probes.client(probes.build()) as http:
        response = await http.post(
            probes.BODY,
            content=b'{"name": "x", "limit": ',
            headers={"X-Access-Token": probes.PASS, "Content-Type": "application/json"},
        )
    [error] = response.json()["errors"]
    assert (response.status_code, error["type"], error["loc"][0]) == (422, "json_invalid", "body")
    assert '"name"' not in response.text


async def test_a44_bare_library_400_is_invalid_with_empty_errors() -> None:
    """A44: один байт `\\xff` с `application/json` — 400, тело ровно форма `invalid` с пустым
    `errors`: голый `HTTPException` библиотеки в форме отказа, без его текста (проба 7)."""
    async with probes.client(probes.build()) as http:
        response = await http.post(
            probes.BODY,
            content=b"\xff",
            headers={"X-Access-Token": probes.PASS, "Content-Type": "application/json"},
        )
    assert response.status_code == 400
    assert response.json() == {**INVALID, "errors": []}
    assert "There was an error parsing the body" not in response.text


BARE = [
    ("POST", probes.CLOSED, 405, "method-not-allowed"),
    ("GET", probes.BARE_503, 500, "internal"),
]


@pytest.mark.parametrize(("method", "path", "status", "kind"), BARE)
async def test_a44_bare_405_and_5xx_of_the_library_take_the_contract_form(
    method: str, path: str, status: int, kind: str
) -> None:
    """A44: голый `HTTPException` библиотеки — в форме договора. `POST` закрытой пробы с
    пропуском — 405 с `Allow` по образцу `method-not-allowed`; `HTTPException(503)` с меткой —
    500 `internal` с номером из `X-Request-ID`, метки в ответе нет."""
    async with probes.client(probes.build()) as http:
        response = await http.request(method, path, headers={"X-Access-Token": probes.PASS})
    sample, body = probes.sample(kind)["body"], response.json()
    assert (response.status_code, body["kind"]) == (status, kind)
    if kind == "internal":
        number = response.headers["x-request-id"]
        assert body == {**sample, "detail": sample["detail"].replace("0" * 32, number)}
        assert "bare-503-mark-5e1f" not in response.text
    else:
        assert (response.headers.get("allow"), body) == ("GET", sample)


async def test_a45_crash_is_a_500_in_form_and_one_error_record(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A45: проба падает `RuntimeError("marker-c0ffee")`; клиент с `raise_app_exceptions=True`
    получает ответ, а не исключение: 500 по образцу `internal`, номер в `detail` — из
    `X-Request-ID`, метки в ответе нет. В журнале ровно одна запись ERROR с трассировкой, меткой
    и тем же `request_id`."""
    log_setup.setup_logging(fmt="json")
    internal = probes.sample("internal")
    async with probes.client(probes.build(), raise_app_exceptions=True) as http:
        response = await http.get(probes.BOOM, headers={"X-Access-Token": probes.PASS})
    number = response.headers["x-request-id"]
    found = re.fullmatch(internal["detail_pattern"], response.json()["detail"])
    assert response.status_code == 500 and found and found.group(1) == number
    assert re.fullmatch(internal["header_patterns"]["x-request-id"], number)
    assert "marker-c0ffee" not in response.text
    records = [json.loads(line) for line in capsys.readouterr().err.splitlines() if line]
    errors = [r for r in records if r["level"] == logging.getLevelName(logging.ERROR)]
    assert [(r["request_id"], "marker-c0ffee" in r.get("exc", "")) for r in errors] == [
        (number, True)
    ]
