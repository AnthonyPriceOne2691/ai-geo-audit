"""Запрет сети в тестах (A30): открыта только петля, отказ — словами и сразу."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Callable
from typing import Any

import netguard
import pytest

FAR = ("198.51.100.7", 443)
FAR_TEXT = "тест ходит только на петлю: 198.51.100.7:443"


@pytest.fixture(scope="session")
def tried_from_session_fixture() -> str:
    """Фикстура уровня сессии пробует уйти в сеть раньше любого теста."""
    try:
        socket.create_connection(FAR, timeout=1)
    except OSError as exc:
        return str(exc)
    return "соединилась"


def test_a30_ban_stands_before_session_fixtures(tried_from_session_fixture: str) -> None:
    """A30: запрет стоит с импорта `conftest.py` — фикстура уровня сессии получает отказ словами
    и подсказку, что делать."""
    assert FAR_TEXT in tried_from_session_fixture
    assert "поднимите сервер на 127.0.0.1" in tried_from_session_fixture


def _asyncio_open(address: tuple[str, int]) -> None:
    asyncio.run(asyncio.open_connection(*address))


def _connect_ex(address: tuple[str, int]) -> None:
    with socket.socket() as sock:
        sock.connect_ex(address)


@pytest.mark.parametrize(
    "attempt", [lambda a: socket.create_connection(a, timeout=1), _connect_ex, _asyncio_open]
)
def test_a30_public_address_is_refused_in_words(attempt: Callable[[Any], None]) -> None:
    """A30: `create_connection`, `connect_ex` и `asyncio.open_connection` на 198.51.100.7:443 —
    `OSError` со словами; `connect_ex` тоже отказывает исключением, а не кодом ошибки."""
    with pytest.raises(OSError, match=FAR_TEXT):
        attempt(FAR)


async def _loop_resolve(host: str) -> object:
    return await asyncio.get_running_loop().getaddrinfo(host, 443)


RESOLVERS = [
    lambda h: socket.getaddrinfo(h, 443),
    lambda h: asyncio.run(_loop_resolve(h)),
    socket.gethostbyname,
    socket.gethostbyname_ex,
]


@pytest.mark.parametrize("resolve", RESOLVERS)
@pytest.mark.parametrize("host", ["example.com", "api.localhost", "db"])
def test_a30_names_outside_loopback_are_refused_before_dns(
    monkeypatch: pytest.MonkeyPatch, resolve: Callable[[str], object], host: str
) -> None:
    """A30: имя `example.com`, `api.localhost` и имя без точки `db` — так зовутся сервисы compose
    и машины в сети — `socket.gaierror` словами у всех четырёх способов, до любого запроса к
    распознавателю системы; распознаётся только `localhost`. `db` держит порчу G11 проверяющего
    «имя без точки идёт в распознаватель»."""
    calls: list[object] = []
    for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex"):
        monkeypatch.setitem(netguard.REAL, name, lambda *a, **_: calls.append(a))
    with pytest.raises(socket.gaierror, match=f"тест ходит только на петлю: имя '{host}'"):
        resolve(host)
    assert calls == []
    resolve("localhost")
    assert calls


def _guarded(*names: str) -> None:
    """A30: подмена стоит — иначе тест сам ушёл бы в DNS настоящим распознавателем."""
    for name in names:
        assert getattr(socket, name).__module__ == netguard.__name__, f"{name} не под запретом"


async def _loop_nameinfo(host: str) -> object:
    return await asyncio.get_running_loop().getnameinfo((host, 443))


REVERSE = [
    lambda h: socket.gethostbyaddr(h),
    lambda h: socket.getnameinfo((h, 443), 0),
    lambda h: asyncio.run(_loop_nameinfo(h)),
]


@pytest.mark.parametrize("lookup", REVERSE)
@pytest.mark.parametrize("host", ["198.51.100.7", "example.com", "::ffff:8.8.8.8"])
def test_a30_reverse_lookups_outside_loopback_are_refused_before_dns(
    monkeypatch: pytest.MonkeyPatch, lookup: Callable[[str], object], host: str
) -> None:
    """A30: обратный запрос — `gethostbyaddr`, `getnameinfo` и `getnameinfo` цикла событий — на
    198.51.100.7, `example.com` и `::ffff:8.8.8.8`: `socket.gaierror` словами до распознавателя.
    Запрос PTR ушёл бы в DNS и для строгой записи адреса; петля проходит."""
    _guarded("gethostbyaddr", "getnameinfo")
    calls: list[object] = []
    for name in ("gethostbyaddr", "getnameinfo"):
        monkeypatch.setitem(netguard.REAL, name, lambda *a, **_: calls.append(a) or ("lo", []))
    refusal = f"тест ходит только на петлю: обратный запрос '{host}'"
    with pytest.raises(socket.gaierror, match=refusal):
        lookup(host)
    assert calls == []
    lookup("127.0.0.1")
    assert calls


def test_a30_getfqdn_does_not_reach_the_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    """A30: `socket.getfqdn("example.com")` зовёт `gethostbyaddr` из модуля `socket`, получает
    отказ и возвращает имя как есть: настоящий распознаватель не вызван."""
    _guarded("gethostbyaddr")
    calls: list[object] = []
    monkeypatch.setitem(netguard.REAL, "gethostbyaddr", lambda *a, **_: calls.append(a))
    assert socket.getfqdn("example.com") == "example.com"
    assert calls == []


@pytest.mark.parametrize(
    ("kind", "address"),
    [
        (socket.SOCK_STREAM, ("example.com", 443)),
        (socket.SOCK_DGRAM, ("example.com", 53)),
        (socket.SOCK_STREAM, ("db", 5432)),
        (socket.SOCK_STREAM, ("127.1", 443)),
        (socket.SOCK_STREAM, ("2130706433", 443)),
    ],
)
@pytest.mark.parametrize("call", ["connect", "connect_ex", "sendto"])
def test_a30_names_and_loose_forms_in_the_socket_are_refused(
    kind: int, address: tuple[str, int], call: str
) -> None:
    """A30: имя, имя без точки `db` и нестрогие записи петли прямо в сокете (`connect`,
    `connect_ex`, `sendto`) — отказ: всё, что не разобралось как адрес петли или `localhost`, —
    мимо `getaddrinfo` нельзя."""
    with socket.socket(socket.AF_INET, kind) as sock, pytest.raises(OSError, match="на петлю"):
        if call == "sendto":
            sock.sendto(b"x", address)
        else:
            getattr(sock, call)(address)


def test_a30_udp_datagram_is_refused() -> None:
    """A30: UDP-датаграмма `sendto` на 198.51.100.7:53 — отказ с той же причиной."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        with pytest.raises(OSError, match="тест ходит только на петлю: 198.51.100.7:53"):
            sock.sendto(b"x", ("198.51.100.7", 53))


OPEN = ["127.0.0.1", "127.5.6.7", "::1", "::ffff:127.0.0.1", "localhost"]
SHUT = ["10.1.2.3", "172.16.5.4", "192.168.1.1", "::ffff:10.1.2.3", "172.32.0.1"]
SHUT += ["169.254.169.254", "100.64.0.1", "192.0.2.1", "8.8.8.8"]
SHUT += ["2001:4860:4860::8888", "::ffff:8.8.8.8", "fe80::1"]


def test_a30_only_loopback_is_open() -> None:
    """A30: можно — `127.0.0.1`, `127.5.6.7`, `::1`, петля в записи IPv6 и `localhost`; отказ —
    частные сети, метаданные облака, общие и документальные адреса, IPv6 вне петли. `AF_UNIX`
    назван явно и проходит."""
    assert [netguard.allowed(host) for host in OPEN] == [True] * len(OPEN)
    for host in SHUT:
        with pytest.raises(OSError, match=f"тест ходит только на петлю: {host}:1"):
            netguard.check(socket.AF_INET6 if ":" in host else socket.AF_INET, (host, 1))
    netguard.check(socket.AF_UNIX, "/tmp/probe.sock")


async def test_a30_loopback_server_answers_by_address_and_by_name() -> None:
    """A30: тест поднимает сервер на 127.0.0.1 и ходит к нему по адресу и по имени `localhost`;
    тесты базы ходят в Postgres на петле — их прогон и есть проба."""

    async def answer(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(b"ok")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(answer, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        for host in ("127.0.0.1", "localhost"):
            reader, writer = await asyncio.open_connection(host, port)
            assert await reader.read() == b"ok"
            writer.close()
            await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()
