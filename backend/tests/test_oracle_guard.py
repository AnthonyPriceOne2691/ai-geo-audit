"""Обвязка оракула стека без Docker (A74, A75): замок одной копии, уборка своего имени, сигналы.

Модуль `scripts/oracle_guard.py` лежит вне пакета и грузится по пути файла. Исполнитель команд
Docker подставной: отвечает на запросы по метке проекта compose и пишет каждую команду.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import signal
import sys
from pathlib import Path

import dbtools
import pytest

_spec = importlib.util.spec_from_file_location(
    "oracle_guard", dbtools.ROOT / "scripts" / "oracle_guard.py"
)
assert _spec is not None
assert _spec.loader is not None
guard = importlib.util.module_from_spec(_spec)
sys.modules["oracle_guard"] = guard
_spec.loader.exec_module(guard)

OWN = "ai-geo-audit-oracle-0123456789"
DEV = "ai-geo-audit-dev"
LABEL = "label=com.docker.compose.project="


class Leftovers:
    """Подставной исполнитель: остатки двух стеков — своего имени и стека разработки."""

    def __init__(self) -> None:
        self.stacks = {
            OWN: {"container": ["c-own-1"], "volume": ["v-own-db"], "network": ["n-own"]},
            DEV: {"container": ["c-dev-1"], "volume": ["v-dev-db"], "network": ["n-dev"]},
        }
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, *args: str, limit: str = "run", check: bool = True) -> str:
        self.commands.append(args)
        if len(args) > 1 and args[1] == "ls":
            marks = [a[len(LABEL) :] for a in args if a.startswith(LABEL)]
            stacks = [self.stacks[m] for m in marks if m in self.stacks] if marks else []
            if not marks:
                stacks = list(self.stacks.values())
            return "".join(f"{i}\n" for s in stacks for i in s.get(args[0], []))
        return ""


def test_a75_stack_name_is_the_prefix_and_ten_signs_of_its_own_path() -> None:
    """A75, первая строка: имена двух копий разные, каждое — префикс и 10 знаков sha256 своего
    пути; `ai-geo-audit-dev` не выбирается ни для какого пути."""
    paths = [Path("/work/copy-a"), Path("/work/copy-b"), Path("/")]
    names = [guard.stack_name(p) for p in paths]
    assert len(set(names)) == len(names)
    for path, name in zip(paths, names, strict=True):
        digest = hashlib.sha256(str(path).encode()).hexdigest()[:10]
        assert name == f"ai-geo-audit-oracle-{digest}"
        assert re.fullmatch(r"ai-geo-audit-oracle-[0-9a-f]{10}", name)
    assert DEV not in names


def test_a74_start_cleanup_removes_only_its_own_name_and_counts() -> None:
    """A74, четвёртая строка: остатки двух стеков — убрано только своё (контейнер, том, сеть),
    число — три; к `ai-geo-audit-dev` и его объектам команд нет."""
    docker = Leftovers()
    assert guard.start_cleanup(docker, OWN) == 3
    flat = [" ".join(c) for c in docker.commands]
    assert not [c for c in flat if DEV in c or "-dev" in c], flat
    removed = [c for c in docker.commands if "ls" not in c]
    assert removed == [
        ("rm", "-f", "-v", "c-own-1"),
        ("volume", "rm", "-f", "v-own-db"),
        ("network", "rm", "n-own"),
    ]


def test_a74_start_cleanup_with_nothing_left_removes_nothing() -> None:
    """A74: остатков своего имени нет — ноль, ни одной команды удаления."""
    docker = Leftovers()
    docker.stacks.pop(OWN)
    assert guard.start_cleanup(docker, OWN) == 0
    assert [c for c in docker.commands if "ls" not in c] == []


def test_a75_second_lock_of_the_same_copy_is_refused_at_once(tmp_path: Path) -> None:
    """A75, третья строка: второй замок той же копии, пока держит первый, — отказ сразу со словами
    «оракул этой копии уже идёт»; замок первого не снят."""
    with guard.copy_lock(OWN, tmp_path):
        for _ in range(2):
            with (
                pytest.raises(guard.LockBusyError, match="оракул этой копии уже идёт"),
                guard.copy_lock(OWN, tmp_path),
            ):
                pass
    with guard.copy_lock(OWN, tmp_path):
        pass


def test_a75_lock_file_without_an_os_lock_does_not_block(tmp_path: Path) -> None:
    """A75, четвёртая строка: файл замка остался от прогона, убитого `kill -9`, блокировки ОС на
    нём нет — следующий прогон берёт замок; файл замок не удаляет."""
    stale = tmp_path / f"{OWN}.lock"
    stale.write_text("4242\n", encoding="utf-8")
    with guard.copy_lock(OWN, tmp_path) as path:
        assert path == stale
    assert stale.exists()


def test_a75_locks_of_two_copies_do_not_block_each_other(tmp_path: Path) -> None:
    """A75: замки двух копий — разные файлы по имени стека, друг друга не держат."""
    with (
        guard.copy_lock(OWN, tmp_path),
        guard.copy_lock("ai-geo-audit-oracle-9876543210", tmp_path),
    ):
        pass


def interrupted_by(first: signal.Signals, second: signal.Signals | None = None) -> tuple:
    """Ход обвязки: тело получает `first`, уборка — `second`; что вызвано и с каким кодом."""
    calls: list[str] = []

    def body() -> str:
        calls.append("тело")
        os.kill(os.getpid(), first)
        calls.append("тело после сигнала")
        return "итог тела"

    def cleanup() -> list[str]:
        calls.append("уборка")
        if second is not None:
            os.kill(os.getpid(), second)
        calls.append("уборка до конца")
        return []

    before = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    code, outcome, left = guard.guarded(body, cleanup)
    after = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    return code, outcome, left, calls, before == after


@pytest.mark.parametrize(("sent", "code"), [(signal.SIGINT, 130), (signal.SIGTERM, 143)])
def test_a74_signal_stops_the_body_and_cleans_up_once(sent: signal.Signals, code: int) -> None:
    """A74, первая и вторая строки: SIGINT — код 130, SIGTERM — 143; тело прервано, уборка
    одна и до конца; обработчики сигналов после обвязки — прежние."""
    got, outcome, left, calls, restored = interrupted_by(sent)
    assert (got, outcome, left) == (code, None, [])
    assert calls == ["тело", "уборка", "уборка до конца"]
    assert restored


def test_a74_second_signal_during_cleanup_gives_no_second_cleanup() -> None:
    """A74, вторая строка: SIGINT, а за ним SIGTERM посреди уборки, как при отмене джобы на
    GitHub, — уборка одна и доходит до конца, код первого сигнала — 130."""
    got, _, _, calls, restored = interrupted_by(signal.SIGINT, signal.SIGTERM)
    assert got == 130
    assert calls.count("уборка") == 1
    assert calls[-1] == "уборка до конца"
    assert restored


def test_a74_body_without_a_signal_returns_its_outcome_and_cleans_up() -> None:
    """A74: без сигнала — итог тела, уборка одна, кода сигнала нет."""
    calls: list[str] = []
    code, outcome, left = guard.guarded(lambda: "итог", lambda: calls.append("уборка") or ["x"])
    assert (code, outcome, left, calls) == (None, "итог", ["x"], ["уборка"])
