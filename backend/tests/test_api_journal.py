"""Журналы запроса (A36, A46, A47): журнал настраивает `create_app`; номер запроса — присланный по
образцу или свой; строка доступа — одна на запрос, без строки запроса, адрес клиента — из
заголовка прокси только от доверенного соединения."""

from __future__ import annotations

import json
import re

import api_probes as probes
import dbtools
import pytest
import uvicorn
from starlette.types import ASGIApp, Message, Scope

from ai_geo_audit import config
from ai_geo_audit.api import app as app_module
from ai_geo_audit.logs import setup as log_setup

ACCESS = "ai_geo_audit.api.access"
SECRET = "s3cr3t-in-url-123456"
OWN_NUMBER = re.compile(r"[0-9a-f]{32}")
TEXT_RECORD = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}\+0000 [A-Z]+ ")
PASS = {"X-Access-Token": probes.PASS}


def json_records(err: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in err.splitlines() if line.startswith("{")]


async def bare_call(app: ASGIApp, headers: list[tuple[bytes, bytes]]) -> str:
    """`GET /api/nope` прямо в приложение ASGI: заголовки области — как есть, байтами; номер
    запроса из ответа."""
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/nope",
        "raw_path": b"/api/nope",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"host", b"test"), *headers],
        "client": ("127.0.0.1", 40000),
        "server": ("test", 80),
    }
    await app(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    return str(dict(start["headers"])[b"x-request-id"].decode())


async def test_a36_create_app_sets_up_the_journal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A36: `LOG_FORMAT=json`; `create_app()` собран дважды подряд, тест сам `setup_logging()` не
    зовёт; проба с пропуском пишет «проба» с `probe` 7 — в выводе ровно одна такая запись строкой
    JSON: `probe` — 7, `ts` кончается на `+0000`, `request_id` — номер из ответа."""
    monkeypatch.setenv("LOG_FORMAT", "json")
    monkeypatch.setattr(config, "logs", config.LogSettings())
    probes.build()
    async with probes.client(probes.build()) as http:
        response = await http.get(probes.JOURNAL, headers=PASS)
    found = [r for r in json_records(capsys.readouterr().err) if r["message"] == "проба"]
    assert [(r["probe"], str(r["ts"])[-5:], r["request_id"]) for r in found] == [
        (7, "+0000", response.headers["x-request-id"])
    ]


@pytest.mark.parametrize("sent", ["abc-123_DEF.4", "0123456789abcdef" * 4])
async def test_a46_valid_request_id_is_kept(capsys: pytest.CaptureFixture[str], sent: str) -> None:
    """A46: `X-Request-ID: abc-123_DEF.4` и номер ровно из 64 знаков — тот же номер в заголовке
    ответа и в каждой записи журнала этого запроса."""
    app = probes.build()
    log_setup.setup_logging(fmt="json")
    async with probes.client(app) as http:
        response = await http.get(probes.JOURNAL, headers={**PASS, "X-Request-ID": sent})
    records = json_records(capsys.readouterr().err)
    assert response.headers["x-request-id"] == sent
    assert len(records) >= 2 and {r["request_id"] for r in records} == {sent}


BAD_NUMBERS = [
    [(b"x-request-id", b"q" * 65)],
    [(b"x-request-id", b"qq w;7")],
    [(b"x-request-id", "номерок".encode())],
    [(b"x-request-id", b"")],
    [(b"x-request-id", b"twin-1"), (b"x-request-id", b"twin-2")],
    [(b"x-request-id", b"nl-77\n")],
    [],
]


@pytest.mark.parametrize("headers", BAD_NUMBERS)
async def test_a46_other_numbers_are_replaced(
    capsys: pytest.CaptureFixture[str], headers: list[tuple[bytes, bytes]]
) -> None:
    """A46: `X-Request-ID` из 65 знаков, с пробелом и точкой с запятой, кириллицей в UTF-8, пустой,
    два заголовка, `abc` с переводом строки — в заголовках области ASGI; без заголовка — свой номер
    из 32 шестнадцатеричных знаков, присланный не встречается в выводе. Два запроса без заголовка —
    разные номера."""
    app = probes.build()
    log_setup.setup_logging(fmt="json")
    number = await bare_call(app, headers)
    err = capsys.readouterr().err
    assert OWN_NUMBER.fullmatch(number)
    sent = [value.decode("utf-8").strip() for _, value in headers]
    assert [value for value in sent if value and value in err] == []
    if not headers:
        assert await bare_call(app, []) != number


@pytest.mark.parametrize("fmt", ["json", "text"])
async def test_a47_one_access_line_per_request_without_the_query(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], fmt: str
) -> None:
    """A47: `GET /api/health?token=…` (200) и `GET /api/nope?token=…` без пропуска (403 ворот), в
    `json` и `text`: на каждый запрос ровно одна строка доступа логгером `ai_geo_audit.api.access`
    — метод, путь, код, `duration_ms` — целое ≥ 0, номер из ответа, `client` — `127.0.0.1`; в
    `text` хвост несёт `client=127.0.0.1` и `path=/api/…`. Строки запроса в выводе нет."""
    probes.use_database(monkeypatch, dbtools.own_url())
    app = probes.build()
    log_setup.setup_logging(fmt=fmt)
    async with probes.client(app) as http:
        answers = [
            await http.get(f"{path}?token={SECRET}") for path in ("/api/health", "/api/nope")
        ]
    err = capsys.readouterr().err
    lines = [line for line in err.splitlines() if ACCESS in line]
    assert SECRET not in err and len(lines) == 2
    if fmt == "text":
        assert all("client=127.0.0.1" in line for line in lines)
        assert ["path=/api/health" in lines[0], "path=/api/nope" in lines[1]] == [True, True]
        return
    records = [json.loads(line) for line in lines]
    expected = [
        (ACCESS, "GET", path, status, answer.headers["x-request-id"], "127.0.0.1")
        for path, status, answer in zip(("/api/health", "/api/nope"), (200, 403), answers)
    ]
    fields = ("logger", "method", "path", "status", "request_id", "client")
    assert [tuple(r[k] for k in fields) for r in records] == expected
    assert all(isinstance(r["duration_ms"], int) and r["duration_ms"] >= 0 for r in records)


@pytest.mark.parametrize(
    ("peer", "forwarded", "client"),
    [
        ("10.9.9.9", "198.51.100.1, 203.0.113.9", "203.0.113.9"),
        ("192.0.2.50", "203.0.113.9", "192.0.2.50"),
    ],
)
async def test_a47_client_comes_from_the_header_only_via_a_trusted_proxy(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    peer: str,
    forwarded: str,
    client: str,
) -> None:
    """A47: приложение, собранное uvicorn из `server_settings()` с его слоем заголовков прокси,
    `API_TRUSTED_PROXIES=10.9.9.9`. Соединение от `10.9.9.9` с `X-Forwarded-For: 198.51.100.1,
    203.0.113.9` — `client` — `203.0.113.9`; от `192.0.2.50` с `X-Forwarded-For: 203.0.113.9` —
    `192.0.2.50`: заголовок недоверенного соединения не действует."""
    monkeypatch.setenv("API_TRUSTED_PROXIES", "10.9.9.9")
    monkeypatch.setattr(config, "api", config.ApiSettings())
    server = uvicorn.Config(**app_module.server_settings())
    server.load()
    log_setup.setup_logging(fmt="json")
    async with probes.client(server.loaded_app, address=(peer, 40000)) as http:
        await http.get("/api/nope", headers={"X-Forwarded-For": forwarded})
    records = [r for r in json_records(capsys.readouterr().err) if r["logger"] == ACCESS]
    assert [r["client"] for r in records] == [client]


@pytest.mark.parametrize("fmt", ["json", "text"])
async def test_a47_path_keeps_percents_and_one_record_is_one_line(
    capsys: pytest.CaptureFixture[str], fmt: str
) -> None:
    """A47: `GET /api/a%0Ab%0Dc` без пропуска, `json` и `text` — 403; в строке доступа `path` —
    `/api/a%0Ab%0Dc`, с процентами; каждая запись — ровно одна строка вывода: перевод строки из
    пути не подделает следующую запись."""
    app = probes.build()
    log_setup.setup_logging(fmt=fmt)
    async with probes.client(app) as http:
        response = await http.get("/api/a%0Ab%0Dc")
    err = capsys.readouterr().err
    lines = [line for line in err.splitlines() if line]
    access = [line for line in lines if ACCESS in line]
    assert response.status_code == 403 and len(access) == 1
    if fmt == "json":
        assert [json.loads(line)["path"] for line in access] == ["/api/a%0Ab%0Dc"]
    else:
        assert "path=/api/a%0Ab%0Dc" in access[0]
    starts = (lambda line: line.startswith("{")) if fmt == "json" else TEXT_RECORD.match
    assert [line for line in lines if not starts(line)] == []
