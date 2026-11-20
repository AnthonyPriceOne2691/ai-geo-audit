"""Здоровье и одна форма отказов (A38–A41, A43): `GET /api/health` без пропуска — по живой базе
своим соединением под пределом; ответы сервиса — ровно образцы файла договора."""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field

import api_probes as probes
import dbtools
import httpx
import pytest
from sqlalchemy.engine import make_url

from ai_geo_audit.api import contract, health
from ai_geo_audit.logs import setup as log_setup

UNAVAILABLE = probes.sample("unavailable")


@dataclass
class Silent:
    """Сервер «базы» на петле: принимает соединения, молчит, считает принятые."""

    url: str = ""
    accepted: list[asyncio.StreamWriter] = field(default_factory=list)


@pytest.fixture
async def silent_db() -> AsyncIterator[Silent]:
    found = Silent()

    async def hold(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        found.accepted.append(writer)
        await reader.read()

    server = await asyncio.start_server(hold, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    found.url = f"postgresql+asyncpg://audit:probe-words-123456@127.0.0.1:{port}/audit"
    yield found
    for writer in found.accepted:
        writer.close()
    server.close()
    await asyncio.wait_for(server.wait_closed(), 2)


@dataclass
class Relay:
    """Посредник TCP перед тестовой базой: по флагу перестаёт пересылать байты, соединений не
    рвёт; с `freeze_on` замерзает сам, когда клиент шлёт эти байты; `delay` — задержка нового
    соединения до первой пересылки; считает открытые соединения."""

    url: str = ""
    frozen: bool = False
    freeze_on: bytes = b""
    delay: float = 0.0
    open: int = 0


@pytest.fixture
async def relay() -> AsyncIterator[Relay]:
    real = make_url(dbtools.own_url())
    found = Relay()
    links: list[asyncio.StreamWriter] = []

    async def pump(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter, watch: bool = False
    ) -> None:
        with contextlib.suppress(ConnectionError):
            while data := await reader.read(65536):
                if watch and found.freeze_on and found.freeze_on in data:
                    found.frozen = True
                while found.frozen:
                    await asyncio.sleep(0.02)
                writer.write(data)
                await writer.drain()
        writer.close()

    async def link(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        found.open += 1
        await asyncio.sleep(found.delay)
        upstream_reader, upstream = await asyncio.open_connection(real.host, real.port)
        links.extend([writer, upstream])
        try:
            await asyncio.gather(pump(reader, upstream, True), pump(upstream_reader, writer))
        finally:
            found.open -= 1

    server = await asyncio.start_server(link, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    found.url = real.set(host="127.0.0.1", port=port).render_as_string(hide_password=False)
    yield found
    found.frozen = False
    for writer in links:
        writer.close()
    server.close()
    await asyncio.wait_for(server.wait_closed(), 2)


def warnings_of(err: str) -> Iterator[dict[str, object]]:
    for line in err.splitlines():
        if line.startswith("{"):
            record = json.loads(line)
            if record["level"] == "WARNING":
                yield record


async def test_a38_health_is_ok_and_says_nothing_else(monkeypatch: pytest.MonkeyPatch) -> None:
    """A38: тестовая база отвечает; `GET /api/health` без заголовков — 200, тело ровно
    `{"status": "ok"}`: ни версий, ни адресов, ни ревизии миграций."""
    probes.use_database(monkeypatch, dbtools.own_url())
    async with probes.client(probes.build()) as http:
        response = await http.get("/api/health")
    assert (response.status_code, response.json()) == (200, {"status": "ok"})


async def test_a39_closed_database_is_unavailable_in_words(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A39: адрес базы — петля и свободный порт; `GET /api/health` — 503, тело ровно образец
    `unavailable`, без адреса, порта и имени. В журнале — одна запись WARNING «здоровье: база не
    ответила» с `error` — `ConnectionRefusedError` (проба 1), без текста ошибки драйвера."""
    url = probes.closed_port_url()
    probes.use_database(monkeypatch, url)
    app = probes.build()
    log_setup.setup_logging(fmt="json")
    async with probes.client(app) as http:
        response = await http.get("/api/health")
    port = url.rsplit(":", 1)[1].split("/")[0]
    assert (response.status_code, response.json()) == (503, UNAVAILABLE["body"])
    assert port not in response.text and "audit" not in response.text
    records = list(warnings_of(capsys.readouterr().err))
    assert [(r["message"], r["error"]) for r in records] == [
        ("здоровье: база не ответила", "ConnectionRefusedError")
    ]
    assert "Connect call failed" not in json.dumps(records) and port not in json.dumps(records)


async def test_a40_silent_database_is_cut_by_the_limit(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], silent_db: Silent
) -> None:
    """A40: «база» молчит; предел 0,5 с — 503 `unavailable` не позже 1 с, запись WARNING с
    `error` — `TimeoutError` и `limit_seconds` — 0.5. Без подмены предел в коде — 3.0."""
    assert health.HEALTH_DB_LIMIT_SECONDS == 3.0
    probes.use_database(monkeypatch, silent_db.url)
    monkeypatch.setattr(health, "HEALTH_DB_LIMIT_SECONDS", 0.5)
    app = probes.build()
    log_setup.setup_logging(fmt="json")
    async with probes.client(app) as http:
        started = time.monotonic()
        response = await asyncio.wait_for(http.get("/api/health"), 2)
        took = time.monotonic() - started
    assert (response.status_code, response.json()["kind"]) == (503, "unavailable")
    assert took <= 1.0, took
    records = list(warnings_of(capsys.readouterr().err))
    assert [(r["error"], r["limit_seconds"]) for r in records] == [("TimeoutError", 0.5)]


@pytest.mark.parametrize("freeze_on", [b"", b"SELECT 1"], ids=["между проверками", "посреди"])
async def test_a40_frozen_database_is_cut_by_the_limit_too(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    relay: Relay,
    freeze_on: bytes,
) -> None:
    """A40, строже показанного: база замерзает — посредник перестаёт пересылать байты, соединений
    не рвёт: между проверками и посреди проверки, на уже установленном соединении, когда клиент
    шлёт `SELECT 1`. Первый запрос — 200; второй — 503 не позже предела плюс 1 с; после
    разморозки — 200. Соединений у посредника в конце 0: оборванное не утекло, пула нет. Запись
    WARNING одна — `error` — `TimeoutError`, а не класс ошибки драйвера, `limit_seconds` — 0.5."""
    probes.use_database(monkeypatch, relay.url)
    monkeypatch.setattr(health, "HEALTH_DB_LIMIT_SECONDS", 0.5)
    app = probes.build()
    log_setup.setup_logging(fmt="json")
    async with probes.client(app) as http, asyncio.timeout(5):
        first = await http.get("/api/health")
        relay.frozen, relay.freeze_on = not freeze_on, freeze_on
        started = time.monotonic()
        second = await http.get("/api/health")
        took = time.monotonic() - started
        relay.frozen, relay.freeze_on = False, b""
        third = await http.get("/api/health")
        for _ in range(50):
            if relay.open == 0:
                break
            await asyncio.sleep(0.02)
    assert [first.status_code, second.status_code, third.status_code] == [200, 503, 200]
    assert took <= 1.5, took
    assert relay.open == 0
    records = list(warnings_of(capsys.readouterr().err))
    assert [(r["error"], r["limit_seconds"]) for r in records] == [("TimeoutError", 0.5)]


async def test_a40_one_deadline_covers_the_connection_and_the_query(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], relay: Relay
) -> None:
    """A40, строже показанного: срок один на весь поход. Предел 1,0 с; новое соединение
    медленное — 0,8 с до первой пересылки, затем база замерзает на `SELECT 1`: 503 не позже
    1,5 с, а не предел заново от соединения (вышло бы около 1,8 с). Запись WARNING —
    `TimeoutError` и предел 1.0."""
    probes.use_database(monkeypatch, relay.url)
    monkeypatch.setattr(health, "HEALTH_DB_LIMIT_SECONDS", 1.0)
    app = probes.build()
    log_setup.setup_logging(fmt="json")
    async with probes.client(app) as http, asyncio.timeout(5):
        warm = await http.get("/api/health")
        relay.delay, relay.freeze_on = 0.8, b"SELECT 1"
        started = time.monotonic()
        response = await http.get("/api/health")
        took = time.monotonic() - started
        relay.delay, relay.freeze_on, relay.frozen = 0.0, b"", False
    assert (warm.status_code, response.status_code) == (200, 503)
    assert took <= 1.5, took
    records = list(warnings_of(capsys.readouterr().err))
    assert [(r["error"], r["limit_seconds"]) for r in records] == [("TimeoutError", 1.0)]


async def test_a41_requests_without_database_need_do_not_touch_it(
    monkeypatch: pytest.MonkeyPatch, silent_db: Silent
) -> None:
    """A41: молчащий сервер вместо базы; без пропуска `/api/nope`, закрытая проба, `OPTIONS
    /api/health`; с пропуском — закрытая проба: 403, 403, 405 и 200 с именем вошедшего, сервер
    «базы» не принял ни одного соединения. После `GET /api/health` — 503 и ровно одно. Весь
    заход — под пределом 3 с: запрос, который полез в молчащую базу, не повиснет, а покраснеет."""
    probes.use_database(monkeypatch, silent_db.url)
    monkeypatch.setattr(health, "HEALTH_DB_LIMIT_SECONDS", 0.5)
    async with probes.client(probes.build()) as http, asyncio.timeout(3):
        codes = [
            (await http.get("/api/nope")).status_code,
            (await http.get(probes.CLOSED)).status_code,
            (await http.options("/api/health")).status_code,
        ]
        allowed = await http.get(probes.CLOSED, headers={"X-Access-Token": probes.PASS})
        before = len(silent_db.accepted)
        checked = await http.get("/api/health")
    assert codes == [403, 403, 405]
    assert (allowed.status_code, allowed.json()) == (200, {"visitor": "probe-visitor"})
    assert before == 0
    assert (checked.status_code, len(silent_db.accepted)) == (503, 1)


CONTRACT_HEADERS = ("content-type", "x-access-refusal", "allow", "retry-after")


async def nothing() -> AsyncIterator[bytes]:
    """Тело без байтов: размер объявляет заголовок, тело не нужно (проба 12)."""
    for chunk in ():
        yield chunk


async def busy_answer(http: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> object:
    """Запросов в работе столько, сколько позволяет предел (подменён на 1); ещё один — здоровье."""
    from ai_geo_audit.api import limits

    monkeypatch.setattr(limits, "IN_FLIGHT_LIMIT", 1)
    waiting = probes.Waiting()
    monkeypatch.setattr(probes, "WAITING", waiting)
    held = asyncio.create_task(http.get(probes.WAIT, headers={"X-Access-Token": probes.PASS}))
    await waiting.until(1)
    response = await http.get("/api/health")
    waiting.release.set()
    await held
    return response


async def answer(kind: str, monkeypatch: pytest.MonkeyPatch) -> object:
    """Запрос из `when` образца: по одному на вид."""
    token = {"X-Access-Token": probes.PASS}
    probes.use_database(
        monkeypatch, probes.closed_port_url() if kind == "unavailable" else dbtools.own_url()
    )
    async with probes.client(probes.build()) as http:
        if kind in ("health-ok", "unavailable"):
            return await http.get("/api/health")
        if kind == "too-large":
            size = {"Content-Length": "1048577"}
            return await http.post(probes.BODY, content=nothing(), headers=token | size)
        if kind == "busy":
            return await busy_answer(http, monkeypatch)
        if kind == "signed-out":
            return await http.get("/api/nope")
        if kind == "not-found":
            return await http.get("/api/nope", headers=token)
        if kind == "method-not-allowed":
            return await http.post("/api/health")
        if kind == "invalid":
            return await http.post(probes.BODY, json={"limit": 1}, headers=token)
        return await http.get(probes.BOOM, headers=token)


def test_a43_kinds_in_code_are_exactly_the_samples() -> None:
    """A43, A58: набор видов в коде равен набору `id` раздела `samples` без `health-ok`: вид в
    коде без образца или образец без вида — красный."""
    ids = {s["id"] for s in probes.SAMPLES["samples"]} - {"health-ok"}
    assert set(contract.KINDS) == ids


@pytest.mark.parametrize("kind", [s["id"] for s in probes.SAMPLES["samples"]])
async def test_a43_every_answer_is_its_sample(monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    """A43, A58: по каждому образцу — запрос из его `when`; ответ равен образцу: код, заголовки
    договора (и `retry-after` у `busy`), тело. У `internal` — номер по `header_patterns` и
    `detail_pattern`, номер в `detail` равен `x-request-id`; у `invalid` ключи записей `errors` —
    ровно `errors_item_keys`."""
    sample = probes.sample(kind)
    response = await answer(kind, monkeypatch)
    headers = {k: response.headers[k] for k in CONTRACT_HEADERS if k in response.headers}
    assert (response.status_code, headers) == (sample["status"], sample["headers"])
    body = response.json()
    if kind == "internal":
        number = response.headers["x-request-id"]
        assert re.fullmatch(sample["header_patterns"]["x-request-id"], number)
        found = re.fullmatch(sample["detail_pattern"], body["detail"])
        assert found and found.group(1) == number
        assert {**body, "detail": sample["body"]["detail"]} == sample["body"]
        return
    if kind == "invalid":
        assert [sorted(item) for item in body["errors"]] == [sorted(sample["errors_item_keys"])]
    assert body == sample["body"]
