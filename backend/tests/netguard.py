"""Запрет сети в тестах (A30): открыта только петля. Ставится первой строкой `conftest.py` — до
импорта сервиса и до фикстур уровня сессии. Всё, что не разобралось строгим `ip_address` как
адрес петли или не равно `localhost`, — отказ словами: имя прямо в `connect` сокет распознал бы
сам, мимо `getaddrinfo`. Чего не ловит: подпроцессы, сторонний цикл событий, расширения со
своими сокетами, `sendmsg`.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from typing import Any

REFUSAL = "тест ходит только на петлю"
HINT = "запишите ответ или поднимите сервер на 127.0.0.1"
#: Настоящие функции: запрет зовёт их только для петли; тест подменяет их, чтобы увидеть вызов.
REAL: dict[str, Callable[..., Any]] = {
    "connect": socket.socket.connect,
    "connect_ex": socket.socket.connect_ex,
    "sendto": socket.socket.sendto,
    "getaddrinfo": socket.getaddrinfo,
    "gethostbyname": socket.gethostbyname,
    "gethostbyname_ex": socket.gethostbyname_ex,
}


def allowed(host: object) -> bool:
    """Петля: `127.0.0.0/8`, `::1`, `::ffff:127.0.0.0/104` и имя `localhost`; больше ничего."""
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if host == "localhost":
        return True
    try:
        address = ipaddress.ip_address(str(host))
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped.is_loopback
    return address.is_loopback


def check(family: int, address: object) -> None:
    """Отказ `OSError` словами, если адрес сокета — не петля; сокет `AF_UNIX` назван явно."""
    if family == socket.AF_UNIX:
        return
    host, port = (address[0], address[1]) if isinstance(address, tuple) else (address, "?")
    if not allowed(host):
        raise OSError(f"{REFUSAL}: {host}:{port} — {HINT}")


def _name(host: object) -> None:
    """Имя вне петли — отказ до распознавателя. Строгая запись адреса в DNS не ходит: её
    пропускаем, отказ даст `connect` — с адресом и портом."""
    if host is None or allowed(host):
        return
    try:
        ipaddress.ip_address(str(host.decode() if isinstance(host, bytes) else host))
    except ValueError:
        raise socket.gaierror(socket.EAI_NONAME, f"{REFUSAL}: имя {host!r} — {HINT}") from None


def _connect(sock: socket.socket, address: Any) -> None:
    check(sock.family, address)
    REAL["connect"](sock, address)


def _connect_ex(sock: socket.socket, address: Any) -> int:
    check(sock.family, address)
    return int(REAL["connect_ex"](sock, address))


def _sendto(sock: socket.socket, data: bytes, *rest: Any) -> int:
    check(sock.family, rest[-1])
    return int(REAL["sendto"](sock, data, *rest))


def _resolver(name: str) -> Callable[..., Any]:
    def guarded(host: Any, *args: Any, **kwargs: Any) -> Any:
        _name(host)
        return REAL[name](host, *args, **kwargs)

    return guarded


def install() -> None:
    """Поставить запрет на весь процесс pytest; `create_connection` и цикл `asyncio` идут через
    подменённые `getaddrinfo` и `connect`."""
    socket.socket.connect = _connect  # type: ignore[method-assign,assignment]
    socket.socket.connect_ex = _connect_ex  # type: ignore[method-assign,assignment]
    socket.socket.sendto = _sendto  # type: ignore[method-assign,assignment]
    for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex"):
        setattr(socket, name, _resolver(name))
