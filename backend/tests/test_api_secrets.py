"""Секреты запроса (A48): пропуск по форме и пароль сайта из `Authorization: Basic` вычищаются из
записей любого логгера в своём запросе — и из записи перехвата; мусор в заголовках запрос не
роняет; секрет запроса не копится в общем наборе вычистки."""

from __future__ import annotations

import base64
import json

import api_probes as probes
import dbtools
import pytest
from starlette.types import ASGIApp, Message, Scope

from ai_geo_audit.logs import setup as log_setup

ACCESS = "ai_geo_audit.api.access"
#: Схема, пара «имя:пароль» и пароль сайта, который вычистка обязана узнать.
VARIANTS = [
    ("Basic", "user:proxy-pass-words", "proxy-pass-words"),
    ("basic", "user:proxy-pass-words", "proxy-pass-words"),
    ("Basic", "user:pass:with-colons-words", "pass:with-colons-words"),
]


def json_records(err: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in err.splitlines() if line.startswith("{")]


@pytest.mark.parametrize("fmt", ["json", "text"])
@pytest.mark.parametrize(("scheme", "pair", "password"), VARIANTS)
async def test_a48_pass_and_site_password_never_reach_the_output(
    capsys: pytest.CaptureFixture[str], fmt: str, scheme: str, pair: str, password: str
) -> None:
    """A48: проба пишет `Authorization`, пропуск и пароль сайта; вторая падает с пропуском в
    тексте. С тестовым пропуском и `Basic` (и `basic` строчными, и паролем с двоеточиями) — в
    выводе нет ни пароля, ни строки после схемы, ни пропуска, на их месте `***`: и в записи
    пробы, и в записи ERROR перехвата с трассировкой, в обоих форматах; у пароля с двоеточиями
    маскируется всё после первого."""
    encoded = base64.b64encode(pair.encode()).decode()
    sent = {"X-Access-Token": probes.PASS, "Authorization": f"{scheme} {encoded}"}
    app = probes.build()
    log_setup.setup_logging(fmt=fmt)
    async with probes.client(app) as http:
        logged = await http.get(probes.HEADERS, headers=sent)
        crashed = await http.get(probes.BOOM_PASS, headers=sent)
    out = capsys.readouterr().err
    assert (logged.status_code, crashed.status_code) == (200, 500)
    assert [value for value in (probes.PASS, encoded, password) if value in out] == []
    if fmt == "text":
        masked = [f"заголовки: {scheme} ***", "password=***", "token=***", "упал с пропуском ***"]
        assert [part for part in masked if part not in out] == []
        return
    records = json_records(out)
    probe = next(r for r in records if r["logger"] == "ai_geo_audit.api.probe")
    assert (probe["message"], probe["token"], probe["password"]) == (
        f"заголовки: {scheme} ***",
        "***",
        "***",
    )
    error = next(r for r in records if r["level"] == "ERROR")
    assert "упал с пропуском ***" in str(error["exc"])


async def test_a48_crashed_request_has_one_access_line_and_numbered_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A48: у падающей пробы — одна строка доступа: `status` — 500, `request_id` — номер из ответа;
    запись ERROR перехват пишет в контексте номера и секретов запроса."""
    sent = {"X-Access-Token": probes.PASS, "Authorization": probes.BASIC}
    app = probes.build()
    log_setup.setup_logging(fmt="json")
    async with probes.client(app) as http:
        crashed = await http.get(probes.BOOM_PASS, headers=sent)
    number = crashed.headers["x-request-id"]
    records = json_records(capsys.readouterr().err)
    access = [(r["path"], r["status"], r["request_id"]) for r in records if r["logger"] == ACCESS]
    assert access == [(probes.BOOM_PASS, 500, number)]
    errors = [r for r in records if r["level"] == "ERROR"]
    assert [(r["request_id"], probes.PASS in str(r["exc"])) for r in errors] == [(number, False)]


def asgi_scope(path: str, headers: list[tuple[bytes, bytes]]) -> Scope:
    """Область запроса ASGI: заголовки — как есть, байтами."""
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"test"), *headers],
        "client": ("127.0.0.1", 40000),
        "server": ("test", 80),
    }


async def bare_call(app: ASGIApp, path: str, headers: list[tuple[bytes, bytes]]) -> Message:
    """Запрос прямо в приложение ASGI; начало ответа и его тело."""
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    await app(asgi_scope(path, headers), receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return {"status": start["status"], "body": body}


GARBAGE = [
    [(b"authorization", b"Basic !!!")],
    [(b"authorization", b"Basic")],
    [(b"authorization", b"Basic /w==")],
    [(b"authorization", b"Basic " + base64.b64encode(b"nocolon"))],
    [(b"authorization", b"Bearer abc")],
    [(b"authorization", b"\xff\xfe")],
    [(b"x-access-token", b"\xff\xfe")],
    [(b"x-access-token", b"junk one"), (b"x-access-token", b"junk two")],
]


@pytest.mark.parametrize("headers", GARBAGE)
async def test_a48_garbage_in_both_headers_does_not_crash(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    headers: list[tuple[bytes, bytes]],
) -> None:
    """A48: мусорный `Authorization` (`Basic !!!`, без значения, байт не UTF-8, без двоеточия,
    `Bearer`, байты `\\xff\\xfe`) и мусорный `X-Access-Token` (байты, два заголовка): здоровье —
    200, закрытая проба без пропуска — 403 образец `signed-out`; ни одного 500, у каждого запроса
    — строка доступа. Разбор обоих заголовков исключений не бросает."""
    probes.use_database(monkeypatch, dbtools.own_url())
    app = probes.build()
    log_setup.setup_logging(fmt="json")
    health = await bare_call(app, "/api/health", headers)
    closed = await bare_call(app, probes.CLOSED, headers)
    signed_out = probes.sample("signed-out")
    assert (health["status"], closed["status"]) == (200, 403)
    assert json.loads(closed["body"]) == signed_out["body"]
    records = json_records(capsys.readouterr().err)
    assert [r["status"] for r in records if r["logger"] == ACCESS] == [200, 403]


async def test_a48_only_the_pass_form_is_a_secret_and_it_does_not_pile_up(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A48: `X-Access-Token: /api/nope` и `GET /api/nope` — значение не по форме пропуска, не
    секрет: в строке доступа `path` — `/api/nope`, а не `***`. 200 запросов с разными значениями
    по форме пропуска — общий набор секретов вычистки того же размера: секрет живёт в запросе."""
    app = probes.build()
    log_setup.setup_logging(fmt="json")
    before = len(log_setup._secrets)
    async with probes.client(app) as http:
        await http.get("/api/nope", headers={"X-Access-Token": "/api/nope"})
        for n in range(200):
            token = f"eyJplaceholder.test-pass-{n}.not-a-signature"
            await http.get("/api/nope", headers={"X-Access-Token": token})
    first = next(r for r in json_records(capsys.readouterr().err) if r["logger"] == ACCESS)
    assert first["path"] == "/api/nope"
    assert len(log_setup._secrets) == before
