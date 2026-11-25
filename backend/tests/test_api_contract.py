"""Ворота входа и договор отказов (A42, A44, A45, A49, A50): путь вне списка открытых — только
с годным пропуском и до чтения тела; отказы — одной формой по файлу образцов; на каждом ответе —
по одной копии `no-store`, `nosniff` и номера запроса; заголовков CORS нет."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator

import api_probes as probes
import dbtools
import httpx
import pytest

from ai_geo_audit.api import access, limits
from ai_geo_audit.api.app import create_app
from ai_geo_audit.logs import setup as log_setup

SIGNED_OUT = probes.sample("signed-out")


def refused_signed_out(response: object) -> list[str]:
    """Что не так с ответом «нужен вход»; пусто — по образцу `signed-out` (у `HEAD` — без тела)."""
    problems = []
    if response.status_code != 403:
        problems.append(f"код {response.status_code}")
    if response.headers.get("x-access-refusal") != "signed-out":
        problems.append("нет X-Access-Refusal: signed-out")
    if "www-authenticate" in response.headers:
        problems.append("есть WWW-Authenticate")
    if response.request.method != "HEAD" and response.json() != SIGNED_OUT["body"]:
        problems.append(f"тело {response.json()}")
    return problems


OTHER_METHODS = ("HEAD", "OPTIONS", "PUT", "PATCH", "DELETE")
WITHOUT_PASS = [
    ("GET", probes.CLOSED, {}),
    ("GET", probes.CLOSED, {"Authorization": "Bearer abc"}),
    ("GET", probes.CLOSED, {"Authorization": probes.BASIC}),
    ("GET", probes.CLOSED, {"X-Access-Token": "any-value-1234567890"}),
    ("GET", "/api/nope", {}),
    ("GET", "/", {}),
    ("GET", "/api/health/", {}),
    ("GET", "/api/health/x", {}),
    *[(method, path, {}) for method in OTHER_METHODS for path in (probes.CLOSED, "/api/nope")],
]


@pytest.mark.parametrize(("method", "path", "headers"), WITHOUT_PASS)
async def test_a42_without_a_valid_pass_everything_is_signed_out(
    method: str, path: str, headers: dict[str, str]
) -> None:
    """A42: ворота с настоящей проверкой (до Ч1.8 она не признаёт ни одного пропуска): закрытая
    проба без заголовков, с `Authorization` `Bearer` и `Basic`, с чужим `X-Access-Token`;
    `/api/nope`; `/`; `/api/health/` и `/api/health/x`; закрытая проба и `/api/nope` методами
    `HEAD`, `OPTIONS`, `PUT`, `PATCH`, `DELETE` — всё 403 по образцу `signed-out`, без
    `WWW-Authenticate`. Есть адрес или нет, метод любой — ответ один; путь — точным равенством."""
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
    probes.CALLS.clear()
    app = probes.build(check=probes.broken_check)
    log_setup.setup_logging(fmt="json")
    async with probes.client(app) as http:
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
    """A42: путь закрытой пробы и `/api/nope` по ошибке внесены в список открытых. Без пропуска
    закрытая проба — 403 `signed-out` от зависимости закрытого роутера: вошедшего в состоянии
    запроса нет; `/api/nope` — 404 `not-found`: ворота этот список видят и пропустили."""
    probes.CALLS.clear()
    mistaken = {**access.OPEN_PATHS, probes.CLOSED: "ошибка списка", "/api/nope": "ошибка списка"}
    async with probes.client(probes.build(open_paths=mistaken)) as http:
        response = await http.get(probes.CLOSED)
        missing = await http.get("/api/nope")
    assert refused_signed_out(response) == []
    assert (missing.status_code, missing.json()) == (404, probes.sample("not-found")["body"])
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
    internal = probes.sample("internal")
    app = probes.build()
    log_setup.setup_logging(fmt="json")
    async with probes.client(app, raise_app_exceptions=True) as http:
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


OWN_NUMBER = re.compile(r"[0-9a-f]{32}")
PASS = {"X-Access-Token": probes.PASS}


async def nothing() -> AsyncIterator[bytes]:
    """Тело без байтов: размер объявляет заголовок (проба 12)."""
    for chunk in ():
        yield chunk


def response_headers_wrong(response: httpx.Response, number: str | None = None) -> list[str]:
    """Что не так с заголовками ответа; пусто — по одной копии `no-store`, `nosniff` и номера."""
    head = response.headers
    problems = []
    if head.get_list("cache-control") != ["no-store"]:
        problems.append(f"cache-control {head.get_list('cache-control')}")
    if head.get_list("x-content-type-options") != ["nosniff"]:
        problems.append(f"x-content-type-options {head.get_list('x-content-type-options')}")
    numbers = head.get_list("x-request-id")
    good = numbers == [number] if number else len(numbers) == 1 and OWN_NUMBER.fullmatch(numbers[0])
    if not good:
        problems.append(f"x-request-id {numbers}")
    return problems


async def test_a49_every_answer_carries_no_store_nosniff_and_one_number(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A49: ответы всех видов — 200 здоровья, 200 консоли в `dev`, 403 ворот, с пропуском 404 и
    422, 405, 400 голого `HTTPException`, 500 перехвата, 503 `unavailable`, 413 объявленный и
    потоковый (проба 2), 503 `busy`: у каждого ровно по одной копии `cache-control: no-store`,
    `x-content-type-options: nosniff` и `x-request-id` — 32 шестнадцатеричных знака или
    присланный, если его принял слой (A46)."""
    probes.use_database(monkeypatch, dbtools.own_url())
    got: dict[str, httpx.Response] = {}
    async with probes.client(probes.build()) as http:
        got["200 здоровья"] = await http.get("/api/health")
        got["403 ворот"] = await http.get("/api/nope")
        got["404"] = await http.get("/api/nope", headers=PASS)
        got["422"] = await http.post(probes.BODY, json={"limit": 1}, headers=PASS)
        got["405"] = await http.post("/api/health")
        bare = PASS | {"Content-Type": "application/json"}
        got["400 голый"] = await http.post(probes.BODY, content=b"\xff", headers=bare)
        got["500"] = await http.get(probes.BOOM, headers=PASS)
        declared = PASS | {"Content-Length": "1048577"}
        got["413 объявленный"] = await http.post(probes.BODY, content=nothing(), headers=declared)
        got["413 потоковый"] = await http.post(probes.BODY, content=stream([]), headers=PASS)
        monkeypatch.setattr(limits, "IN_FLIGHT_LIMIT", 0)
        got["503 busy"] = await http.get("/api/health")
        monkeypatch.setattr(limits, "IN_FLIGHT_LIMIT", 32)
        probes.use_database(monkeypatch, probes.closed_port_url())
        got["503 unavailable"] = await http.get("/api/health")
        sent = await http.get("/api/nope", headers={"X-Request-ID": "abc-123_DEF.4"})
    async with probes.client(create_app(visitor_for=probes.known_pass)) as http:
        got["200 консоли"] = await http.get("/api/docs")
    statuses = {label: response.status_code for label, response in got.items()}
    assert [label for label, code in statuses.items() if not label.startswith(str(code))] == []
    wrong = {label: response_headers_wrong(response) for label, response in got.items()}
    assert {label: problems for label, problems in wrong.items() if problems} == {}
    assert response_headers_wrong(sent, "abc-123_DEF.4") == []


async def test_a49_route_headers_are_replaced_not_added() -> None:
    """A49: проба сама ставит `Cache-Control: public, max-age=60`, `X-Content-Type-Options: sniff`
    и `X-Request-ID: forged-id-1`; с тестовым пропуском — по одной копии каждого: `no-store`,
    `nosniff` и номер запроса слоя. Заголовки маршрута заменены, а не дописаны."""
    async with probes.client(probes.build()) as http:
        response = await http.get(probes.OWN_HEADERS, headers=PASS)
    assert (response.status_code, response_headers_wrong(response)) == (200, [])


EVIL = {"Origin": "https://evil.example.invalid"}


def cors_headers(response: httpx.Response) -> list[str]:
    return [name for name in response.headers if name.lower().startswith("access-control-")]


async def test_a50_no_cors_headers_on_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    """A50: `Origin` чужого сайта — `GET /api/health` и с пропуском `GET` закрытой пробы: 200 и 200,
    ни одного заголовка, имя которого начинается с `access-control-`. База и Redis — теста: со
    срезом 3 Ч1.14а1 здоровье ходит и в Redis (A243), а умолчание конфига — не он."""
    probes.use_database(monkeypatch, dbtools.own_url())
    async with probes.client(probes.build()) as http:
        health = await http.get("/api/health", headers=EVIL)
        closed = await http.get(probes.CLOSED, headers=EVIL | PASS)
    assert (health.status_code, closed.status_code) == (200, 200)
    assert cors_headers(health) + cors_headers(closed) == []


async def test_a50_preflights_are_not_allowed_by_anyone() -> None:
    """A50: предзапросы с чужим `Origin`: `OPTIONS /api/health` с `Access-Control-Request-Method:
    GET` — 405 образец `method-not-allowed`, `allow: GET`; `OPTIONS` закрытой пробы без пропуска с
    методом `POST` и заголовком `x-access-token` — 403 `signed-out`. Заголовков `access-control-*`
    нет: слой CORS ответил бы на предзапрос сам, до ворот и маршрута."""
    ask_get = EVIL | {"Access-Control-Request-Method": "GET"}
    ask_post = EVIL | {
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-access-token",
    }
    async with probes.client(probes.build()) as http:
        health = await http.options("/api/health", headers=ask_get)
        closed = await http.options(probes.CLOSED, headers=ask_post)
    allowed = probes.sample("method-not-allowed")
    assert (health.status_code, health.headers.get("allow"), health.json()) == (
        405,
        "GET",
        allowed["body"],
    )
    assert refused_signed_out(closed) == []
    assert cors_headers(health) + cors_headers(closed) == []
