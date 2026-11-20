"""Обвязка оракула стека (A74, A75): имя стека, замок одной копии, уборка своего имени, сигналы.

Оракул зовёт её из `main()` точки входа `scripts/stack_oracle.py`:
- имя стека — по пути копии; стек разработки `ai-geo-audit-dev` не выбирается никогда;
- замок одной копии — блокировка ОС (`flock`) на файле во временной папке ОС под именем стека:
  держит блокировка, а не наличие файла — файл от убитого `kill -9` прогона не мешает;
- уборка на старте — остатки прошлого прогона только своего имени, по метке проекта compose;
- сигналы — SIGINT и SIGTERM прерывают тело, уборка идёт ровно одна; первый сигнал глушит оба до
  конца уборки, поэтому второй (отмена джобы на GitHub шлёт SIGINT, затем SIGTERM) второй уборки
  не даёт. Код выхода — 128 + номер первого сигнала: 130 и 143;
- вывод оракула и сканера — `say`: время UTC с Z, строка до 100 знаков — длинная переносится по
  пробелам, продолжение с отступом; строки таблиц не переносятся (спека, Non-functional).
"""

from __future__ import annotations

import fcntl
import hashlib
import signal
import tempfile
import textwrap
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from typing import Protocol

PREFIX = "ai-geo-audit-oracle-"
LOCK_BUSY = "оракул этой копии уже идёт"
SIGNALS = (signal.SIGINT, signal.SIGTERM)
PROJECT = "label=com.docker.compose.project="
#: Строка вывода — до 100 знаков; продолжение длинной — с этим отступом (спека, Non-functional).
WIDTH = 100
INDENT = "    "
#: Остатки по виду: запрос по метке и команда удаления. Порядок — контейнеры раньше томов и сетей.
REMOVAL = {
    "container": (("-a",), ("rm", "-f", "-v")),
    "volume": ((), ("volume", "rm", "-f")),
    "network": ((), ("network", "rm")),
}


class Runner(Protocol):
    """Исполнитель команд Docker оракула: `docker <args>` под пределом, вывод — текст."""

    def __call__(self, *args: str, limit: str = "run", check: bool = True) -> str: ...


class LockBusyError(RuntimeError):
    """Замок копии держит другой прогон."""


class Interrupted(BaseException):
    """Сигнал посреди прогона; `code` — код выхода 128 + номер сигнала."""

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.code = 128 + signum


def wrapped(text: str, width: int = WIDTH) -> list[str]:
    """Строка вывода — в строки до `width` знаков: перенос по пробелам, продолжение с отступом;
    слово длиннее строки режется. Строка таблицы (начинается с `|`) и короткая — как есть."""
    if text.startswith("|") or len(text) <= width:
        return [text]
    return textwrap.wrap(text, width, subsequent_indent=INDENT, break_on_hyphens=False)


def say(text: str) -> None:
    """Строка вывода оракула и сканера: время UTC с Z, перенос до 100 знаков."""
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    print("\n".join(wrapped(f"{stamp} {text}")), flush=True)


def stack_name(path: Path) -> str:
    """Имя стека оракула по пути рабочей копии: префикс и 10 знаков sha256 пути (A61, A75)."""
    return PREFIX + hashlib.sha256(str(path).encode()).hexdigest()[:10]


@contextmanager
def copy_lock(name: str, folder: Path | None = None) -> Iterator[Path]:
    """Замок одной копии на время прогона; занят — `LockBusyError` сразу. Файл не удаляется:
    удаление открыло бы гонку с прогоном, который уже держит открытый файл."""
    path = (folder or Path(tempfile.gettempdir())) / f"{name}.lock"
    with path.open("a", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise LockBusyError(f"{LOCK_BUSY}: замок {path} держит другой прогон") from None
        yield path


def start_cleanup(docker: Runner, name: str) -> int:
    """Уборка на старте: остатки прошлого прогона своего имени по метке проекта; число убранного.
    Каждый запрос несёт метку своего имени — чужих стеков команды не касаются."""
    found = [
        (kind, ident)
        for kind, (flags, _) in REMOVAL.items()
        for ident in docker(
            kind, "ls", "-q", *flags, "--filter", PROJECT + name, limit="docker", check=False
        ).split()
    ]
    for kind, ident in found:
        docker(*REMOVAL[kind][1], ident, limit="docker", check=False)
    return len(found)


def _ignore() -> None:
    for sig in SIGNALS:
        signal.signal(sig, signal.SIG_IGN)


def guarded[T, U](
    body: Callable[[], T], cleanup: Callable[[], U]
) -> tuple[int | None, T | None, U]:
    """Тело под сигналами, уборка — ровно одна и без сигналов. Возврат: код сигнала или `None`,
    итог тела (при сигнале — `None`), итог уборки. Обработчики сигналов после — прежние."""
    before = {sig: signal.getsignal(sig) for sig in SIGNALS}

    def stop(signum: int, _frame: FrameType | None) -> None:
        _ignore()  # от первого сигнала до конца уборки оба глушатся: второй уборки не будет
        raise Interrupted(signum)

    code: int | None = None
    outcome: T | None = None
    try:
        try:
            try:
                for sig in SIGNALS:
                    signal.signal(sig, stop)
                outcome = body()
            finally:
                _ignore()
        except Interrupted as caught:
            code = caught.code
    finally:
        try:
            left = cleanup()
        finally:
            for sig, handler in before.items():
                signal.signal(sig, handler)
    return code, outcome, left
