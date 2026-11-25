"""Подставные серверы Redis на петле — для отказов, которых нельзя добиться у общего Redis теста,
не задев соседние копии: молчащий, полный (`OOM`), без входа (`NOAUTH`).

Сервер — в своём потоке: его зовут и синхронная постановка со своим циклом событий (A240), и
асинхронное здоровье в цикле теста (A243). Отвечает по имени команды, остальным — `default`;
список ответов команды идёт по очереди, последний повторяется.
"""

from __future__ import annotations

import contextlib
import socket
import threading
from collections.abc import Iterator, Mapping

#: Ответы Redis 7.4 дословно (проба среза 0 на образе стека).
OOM = b"-OOM command not allowed when used memory > 'maxmemory'.\r\n"
NOAUTH = b"-NOAUTH Authentication required.\r\n"
#: Рукопожатие RESP3 и ответы полного Redis: `PING` и служебные команды проходят, запись — нет.
FULL = {
    b"HELLO": b"%1\r\n$5\r\nproto\r\n:3\r\n",
    b"CLIENT": b"+OK\r\n",
    b"SELECT": b"+OK\r\n",
    b"PING": b"+PONG\r\n",
}
#: Пароль адреса подставного сервера — тестовое значение, его ищут во всём выводе.
PASSWORD = "fake-redis-pass-123456"  # pragma: allowlist secret


def closed_port() -> int:
    """Порт на петле, где никто не слушает: отказ соединения сразу."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def url(port: int) -> str:
    """Адрес подставного сервера с тестовым паролем; пароль ищут во всём выводе."""
    return f"redis://:{PASSWORD}@127.0.0.1:{port}/0"


def take(buf: bytes) -> tuple[list[bytes] | None, bytes]:
    """Одна команда RESP из буфера: аргументы и остаток; команда не дошла целиком — None."""
    end = buf.find(b"\r\n")
    if not buf.startswith(b"*") or end < 0:
        return None, buf
    pos, args = end + 2, []
    for _ in range(int(buf[1:end])):
        head = buf.find(b"\r\n", pos)
        if head < 0:
            return None, buf
        start = head + 2
        stop = start + int(buf[pos + 1 : head])
        if len(buf) < stop + 2:
            return None, buf
        args.append(buf[start:stop])
        pos = stop + 2
    return args, buf[pos:]


class FakeRedis:
    """Сервер на петле: принимает соединения и отвечает по `replies`, прочим — `default`; без
    ответов (`silent=True`) молчит. Считает принятые соединения и команды."""

    def __init__(
        self,
        replies: Mapping[bytes, bytes | list[bytes]] | None = None,
        default: bytes = OOM,
        silent: bool = False,
    ) -> None:
        self.replies = {
            name: [r] if isinstance(r, bytes) else list(r) for name, r in (replies or {}).items()
        }
        self.default, self.silent = default, silent
        self.accepted = 0
        self.commands: list[bytes] = []
        self._conns: list[socket.socket] = []
        self._sock = socket.create_server(("127.0.0.1", 0))
        self.port = self._sock.getsockname()[1]
        self.url = url(self.port)
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            self.accepted += 1
            self._conns.append(conn)
            if not self.silent:
                threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def reply(self, name: bytes) -> bytes:
        queue = self.replies.get(name)
        if not queue:
            return self.default
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def _serve(self, conn: socket.socket) -> None:
        buf = b""
        with contextlib.suppress(OSError):
            while data := conn.recv(65536):
                buf += data
                while True:
                    command, buf = take(buf)
                    if command is None:
                        break
                    name = command[0].upper()
                    self.commands.append(name)
                    conn.sendall(self.reply(name))

    def close(self) -> None:
        self._sock.close()
        for conn in self._conns:
            conn.close()


@contextlib.contextmanager
def running(**options: object) -> Iterator[FakeRedis]:
    """Подставной сервер на время блока; после — закрыт со всеми соединениями."""
    server = FakeRedis(**options)  # type: ignore[arg-type]
    try:
        yield server
    finally:
        server.close()
