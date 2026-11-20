"""Проба здоровья для compose — `python -m ai_geo_audit.api.probe` (A70).

Серверы теста — на петле, порт — свободный от системы. Код 0 только на 200; иначе код 1 и одна
строка причины без адреса и секретов. Молчащий сервер — код 1 через 3,0–3,5 с.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import dbtools
import pytest

from ai_geo_audit.api import probe

OK = b'HTTP/1.1 200 OK\r\nContent-Length: 16\r\n\r\n{"status": "ok"}'
DOWN = b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\n\r\n"
#: Молчащий сервер держит соединение не дольше: проба без предела краснеет, а не висит.
SILENCE_SECONDS = 6.0


@contextmanager
def server(reply: bytes | None) -> Iterator[int]:
    """A70: сервер на петле; на каждое соединение — `reply`, а `None` — принять и молчать."""
    listener = socket.create_server(("127.0.0.1", 0))
    listener.settimeout(0.05)
    stop = threading.Event()
    held: list[tuple[socket.socket, threading.Timer]] = []

    def serve() -> None:
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except TimeoutError:
                continue
            if reply is None:
                timer = threading.Timer(SILENCE_SECONDS, conn.close)
                timer.daemon = True
                timer.start()
                held.append((conn, timer))
                continue
            with conn:
                conn.settimeout(2)
                conn.recv(1024)
                conn.sendall(reply)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield listener.getsockname()[1]
    finally:
        stop.set()
        thread.join(timeout=2)
        listener.close()
        for conn, timer in held:
            timer.cancel()
            conn.close()


def closed_port() -> int:
    """A70: порт, свободный от системы, на котором никто не слушает."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def entry(port: object) -> subprocess.CompletedProcess[str]:
    """A70: проба подпроцессом с окружением с нуля; `PYTHONPATH` проходит — порчам."""
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONUTF8": "1", "ENV_FILE": os.devnull}
    env.update({k: v for k, v in os.environ.items() if k == "PYTHONPATH"})
    return subprocess.run(
        [sys.executable, "-m", "ai_geo_audit.api.probe"],
        cwd=dbtools.BACKEND,
        env=env | {"API_PORT": str(port)},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def one_clean_line(text: str, port: object) -> bool:
    """A70: одна строка причины — без адреса, порта и трассировки."""
    lines = text.strip().splitlines()
    leaks = ("127.0.0.1", "localhost", str(port), "Traceback")
    return len(lines) == 1 and not any(leak in text for leak in leaks)


def test_a70_probe_exits_zero_only_on_200() -> None:
    """A70: ответ 200 — код 0; ответ 503 и закрытый порт — код 1 и одна строка причины."""
    with server(OK) as port:
        assert entry(port).returncode == 0
    with server(DOWN) as port:
        done = entry(port)
    assert done.returncode == 1
    assert one_clean_line(done.stdout + done.stderr, port) and "503" in done.stdout
    port = closed_port()
    done = entry(port)
    assert done.returncode == 1
    assert one_clean_line(done.stdout + done.stderr, port)


def test_a70_silent_server_fails_after_three_seconds_not_sooner_not_later() -> None:
    """A70: сервер принял соединение и молчит — код 1 через 3,0–3,5 с: предел 3 с."""
    with server(None) as port:
        started = time.monotonic()
        code, reason = probe.check(port)
        elapsed = time.monotonic() - started
    assert code == 1 and one_clean_line(reason, port)
    assert 3.0 <= elapsed <= 3.5, elapsed


def test_a70_bad_setting_is_one_line_without_the_value() -> None:
    """A70: негодный `API_PORT` — код 1 и одна строка без введённого значения (урок L30)."""
    done = entry("port-9x7q")
    assert done.returncode == 1
    assert one_clean_line(done.stdout + done.stderr, "port-9x7q")


def test_a70_connect_is_under_the_same_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """A70: соединение — под тем же пределом 3 с: зависший `connect` не уводит пробу за срок."""
    limits: list[object] = []

    def hang(address: tuple[str, int], timeout: object = None) -> socket.socket:
        limits.append(timeout)
        raise TimeoutError

    monkeypatch.setattr(probe.socket, "create_connection", hang)
    assert probe.check(closed_port()) == (1, "здоровье: нет ответа за 3 с")
    assert limits == [3.0]


def test_a70_answer_that_is_not_http_is_refused() -> None:
    """A70: первая строка без `HTTP/` — не ответ HTTP: код 1, даже если второе слово — 200."""
    with server(b"foo 200 OK\r\n\r\n") as port:
        assert probe.check(port) == (1, "здоровье: ответ не HTTP")
