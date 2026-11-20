"""Пределы входа (A53, A54, A58): тело — не больше 1 МБ на любом пути после ворот, объявленное —
до чтения, потоковое — не дальше предела и одного куска; запросов в работе — не больше 32 на
процесс, сверх — `busy` сразу; оба отказа — образцы договора, числа в текстах держат константы."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from types import ModuleType

import api_probes as probes
import dbtools
import httpx
import pytest
from starlette.types import Message, Scope

from ai_geo_audit.api import contract
from ai_geo_audit.logs import setup as log_setup

PASS = {"X-Access-Token": probes.PASS}
CHUNK = 65536
LIMIT = 1_048_576
CONTRACT_HEADERS = ("content-type", "x-access-refusal", "allow", "retry-after")
#: Тело JSON пробы ровно в 30 байт.
BODY_30 = b'{"name": "xxxxxx", "limit": 1}'


@pytest.fixture
def limits() -> ModuleType:
    """Модуль пределов — при вызове: до кода среза тест красный, а не сбор тестов."""
    from ai_geo_audit.api import limits

    return limits


@pytest.fixture
def waiting(monkeypatch: pytest.MonkeyPatch) -> probes.Waiting:
    """Своя проба ожидания у каждого теста: события не переходят между циклами событий."""
    fresh = probes.Waiting()
    monkeypatch.setattr(probes, "WAITING", fresh)
    return fresh


def warnings_of(err: str) -> list[dict[str, object]]:
    records = [json.loads(line) for line in err.splitlines() if line.startswith("{")]
    return [r for r in records if r["level"] == "WARNING"]


def differs(response: httpx.Response, kind: str) -> dict[str, object]:
    """Расхождение ответа с образцом вида: код, заголовки договора, тело; пусто — равны."""
    sample = probes.sample(kind)
    head = {k: response.headers[k] for k in CONTRACT_HEADERS if k in response.headers}
    got = (response.status_code, head, response.json())
    want = (sample["status"], sample["headers"], sample["body"])
    return {} if got == want else {"got": got, "want": want}


async def chunks(total: int, sent: list[int]) -> AsyncIterator[bytes]:
    """Тело в `total` байт кусками по 64 КиБ; отданное — в счёт: клиент тянет его из `receive`."""
    while total > 0:
        size = min(CHUNK, total)
        sent.append(size)
        total -= size
        yield b"x" * size


def json_body(size: int) -> bytes:
    """Тело JSON пробы ровно в `size` байт: имя добито до размера."""
    head, tail = b'{"name": "', b'", "limit": 1}'
    return head + b"x" * (size - len(head) - len(tail)) + tail


async def test_a53_declared_body_over_the_limit_is_refused_before_reading(
    capsys: pytest.CaptureFixture[str], limits: ModuleType
) -> None:
    """A53: тестовый пропуск, `POST` пробы с телом, `Content-Length: 1048577`, тело — генератор
    кусками по 64 КиБ со счётом (проба 12): 413 образец `too-large`, из генератора ни байта; одна
    запись WARNING «тело запроса больше предела» — `limit_bytes` 1048576, `declared_bytes`
    1048577. Тот же запрос без пропуска — 403 `signed-out`, тоже ни байта: ворота раньше предела."""
    assert limits.BODY_LIMIT_BYTES == LIMIT
    app = probes.build()
    log_setup.setup_logging(fmt="json")
    sent: list[int] = []
    unsent: list[int] = []
    declared = {"Content-Length": str(LIMIT + 1)}
    async with probes.client(app) as http:
        refused = await http.post(
            probes.BODY, content=chunks(LIMIT + 1, sent), headers=PASS | declared
        )
        gated = await http.post(probes.BODY, content=chunks(LIMIT + 1, unsent), headers=declared)
    assert (differs(refused, "too-large"), differs(gated, "signed-out")) == ({}, {})
    assert (sum(sent), sum(unsent)) == (0, 0)
    found = [
        (r["message"], r["limit_bytes"], r["declared_bytes"])
        for r in warnings_of(capsys.readouterr().err)
    ]
    assert found == [("тело запроса больше предела", LIMIT, LIMIT + 1)]


async def test_a53_streamed_body_stops_at_the_limit(capsys: pytest.CaptureFixture[str]) -> None:
    """A53: тестовый пропуск, 5 МБ потоком без `Content-Length`, куски по 64 КиБ: 413 образец
    `too-large`; из генератора — не больше предела и одного куска: чтение кончилось на пределе
    (проба 2). WARNING — `limit_bytes` 1048576, `read_bytes` 1114112: отказ на семнадцатом куске."""
    app = probes.build()
    log_setup.setup_logging(fmt="json")
    sent: list[int] = []
    async with probes.client(app) as http:
        response = await http.post(probes.BODY, content=chunks(5 * LIMIT, sent), headers=PASS)
    assert differs(response, "too-large") == {}
    assert sum(sent) <= LIMIT + CHUNK
    found = [
        (r["message"], r["limit_bytes"], r["read_bytes"])
        for r in warnings_of(capsys.readouterr().err)
    ]
    assert found == [("тело запроса больше предела", LIMIT, LIMIT + CHUNK)]


async def test_a53_body_at_the_limit_passes_one_byte_more_does_not() -> None:
    """A53: тело JSON пробы ровно 1 048 576 байт — 200 от пробы: тело на пределе проходит; на байт
    длиннее — 413 `too-large`. Отказ — «больше предела», а не «не меньше»: так же судит
    `client_max_body_size` web (A84 Ч1.5б)."""
    typed = PASS | {"Content-Type": "application/json"}
    async with probes.client(probes.build()) as http:
        at = await http.post(probes.BODY, content=json_body(LIMIT), headers=typed)
        over = await http.post(probes.BODY, content=json_body(LIMIT + 1), headers=typed)
    assert (at.status_code, len(at.json()["name"])) == (200, LIMIT - 24)
    assert differs(over, "too-large") == {}


async def test_a53_limit_holds_on_open_paths_too() -> None:
    """A53: без пропуска — открытая проба с телом, 5 МБ потоком без `Content-Length`, и
    `POST /api/health` с `Content-Length: 2000000` без тела: оба — 413 `too-large`, предел
    действует и на открытых путях; из генератора — не больше предела и куска. У здоровья 413, а
    не 405: предел стоит до маршрутизации (пробы 2 и 12)."""
    sent: list[int] = []
    async with probes.client(probes.build_open_body()) as http:
        streamed = await http.post(probes.OPEN_BODY, content=chunks(5 * LIMIT, sent))
        declared = await http.post(
            "/api/health", content=chunks(0, []), headers={"Content-Length": "2000000"}
        )
    assert (differs(streamed, "too-large"), differs(declared, "too-large")) == ({}, {})
    assert sum(sent) <= LIMIT + CHUNK


def body_scope(lengths: list[bytes]) -> Scope:
    """Область `POST` пробы с телом: пропуск и `content-length` — байтами, как их задал тест."""
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": probes.BODY,
        "raw_path": probes.BODY.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"test"),
            (b"x-access-token", probes.PASS.encode()),
            (b"content-type", b"application/json"),
            *[(b"content-length", value) for value in lengths],
        ],
        "client": ("127.0.0.1", 40000),
        "server": ("test", 80),
    }


@pytest.mark.parametrize(
    ("lengths", "status"),
    [([b"abc"], 200), ([b"\xb2"], 200), ([b"-1"], 200), ([b""], 200), ([b"10", b"5000000"], 413)],
)
async def test_a53_declared_size_is_ascii_digits_only(lengths: list[bytes], status: int) -> None:
    """A53: прямой вызов приложения, тело пробы — 30 байт годного JSON. `content-length` — `abc`,
    `\\xb2`, `-1`, пусто: не размер, ответ пробы 200, тело считается по факту. Два заголовка,
    `10` и `5000000`, — 413: размер объявлен любым заголовком из цифр ASCII. Ни одного 500:
    строка latin-1 из `\\xb2` — «²», её пропускает `isdigit()`, а `int()` на ней падает."""
    sent: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": BODY_30, "more_body": False}

    async def send(message: Message) -> None:
        sent.append(message)

    await probes.build()(body_scope(lengths), receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    assert (len(BODY_30), start["status"]) == (30, status)


async def test_a54_over_the_limit_is_busy_at_once(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    limits: ModuleType,
    waiting: probes.Waiting,
) -> None:
    """A54: `IN_FLIGHT_LIMIT` подменён на 2; два запроса с пропуском ждут в пробе ожидания; `GET`
    той же пробы и `GET /api/health` без пропуска — оба сразу 503 `busy`, `retry-after: 60`: проба
    не вызвана, вошедших двое. Здоровье под пределом тоже: с Ч1.3а оно открывает своё соединение к
    базе на каждую проверку, и предел держит их число. На каждый отказ — WARNING «сервис занят» с
    `in_flight` 2 и `limit` 2. Ожидание отпущено — следующий запрос 200. Без подмены — 32."""
    assert limits.IN_FLIGHT_LIMIT == 32
    monkeypatch.setattr(limits, "IN_FLIGHT_LIMIT", 2)
    probes.use_database(monkeypatch, dbtools.own_url())
    app = probes.build()
    log_setup.setup_logging(fmt="json")
    async with probes.client(app) as http, asyncio.timeout(5):
        held = [asyncio.create_task(http.get(probes.WAIT, headers=PASS)) for _ in range(2)]
        await waiting.until(2)
        extra = await http.get(probes.WAIT, headers=PASS)
        health = await http.get("/api/health")
        entered = waiting.entered
        waiting.release.set()
        released = [response.status_code for response in await asyncio.gather(*held)]
        after = await http.get("/api/health")
    assert (differs(extra, "busy"), differs(health, "busy")) == ({}, {})
    assert (entered, released, after.status_code) == (2, [200, 200], 200)
    found = [
        (r["message"], r["in_flight"], r["limit"]) for r in warnings_of(capsys.readouterr().err)
    ]
    assert found == [("сервис занят", 2, 2)] * 2


async def test_a54_every_outcome_frees_its_place(
    monkeypatch: pytest.MonkeyPatch, limits: ModuleType, waiting: probes.Waiting
) -> None:
    """A54: `IN_FLIGHT_LIMIT` подменён на 2; по очереди: проба падает (500), запрос в пробе
    ожидания отменён клиентом, тело 5 МБ потоком (413, проба 2), отказ ворот (403). После каждого
    исхода место освобождено: два запроса снова входят в пробу ожидания, третий — 503 `busy`.
    Мест ровно два — не больше и не меньше."""
    monkeypatch.setattr(limits, "IN_FLIGHT_LIMIT", 2)
    app = probes.build()
    async with probes.client(app) as http, asyncio.timeout(5):
        crashed = await http.get(probes.BOOM, headers=PASS)
        cancelled = asyncio.create_task(http.get(probes.WAIT, headers=PASS))
        await waiting.until(1)
        cancelled.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await cancelled
        streamed = await http.post(probes.BODY, content=chunks(5 * LIMIT, []), headers=PASS)
        gated = await http.get(probes.CLOSED)
        held = [asyncio.create_task(http.get(probes.WAIT, headers=PASS)) for _ in range(2)]
        await waiting.until(3)
        third = await http.get(probes.WAIT, headers=PASS)
        entered = waiting.entered
        waiting.release.set()
        await asyncio.gather(*held)
    assert [crashed.status_code, streamed.status_code, gated.status_code] == [500, 413, 403]
    assert (entered, differs(third, "busy")) == (3, {})


def test_a58_eight_kinds_and_the_numbers_in_both_texts(limits: ModuleType) -> None:
    """A58: видов в коде — восемь, набор равен `id` раздела `samples` без `health-ok` (сверка A43
    в обе стороны; другие разделы файла не читаются). «1 МБ» — это `BODY_LIMIT_BYTES / 2**20`,
    нацело; «через минуту» — это `BUSY_RETRY_AFTER_SECONDS`, 60, он же в `retry-after` образца.
    Константа поменялась без правки текста — тест красный и печатает оба значения."""
    ids = {s["id"] for s in probes.SAMPLES["samples"]} - {"health-ok"}
    assert (len(contract.KINDS), set(contract.KINDS)) == (8, ids)
    megabytes, rest = divmod(limits.BODY_LIMIT_BYTES, 2**20)
    too_large = contract.KINDS["too-large"].detail
    assert (rest, f"не больше {megabytes} МБ" in too_large) == (0, True), (
        limits.BODY_LIMIT_BYTES,
        too_large,
    )
    words = {60: "через минуту"}.get(limits.BUSY_RETRY_AFTER_SECONDS, "—")
    busy = contract.KINDS["busy"].detail
    assert words in busy, (limits.BUSY_RETRY_AFTER_SECONDS, busy)
    retry = probes.sample("busy")["headers"]["retry-after"]
    assert retry == str(limits.BUSY_RETRY_AFTER_SECONDS), (retry, limits.BUSY_RETRY_AFTER_SECONDS)
