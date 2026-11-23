"""Проба хода 0 «Скорости CI» (A280) — только в ветке-пробе, в main не идёт.

Каждая сессия pytest под mutmut пишет строку отметок времени в журнал главного процесса mutmut:
начало настройки, начало сессии, база готова, начало и конец тестов, конец настройки. Сводку
печатает отдельный шаг джобы (`python tests/mutation_timing.py`) из журнала — и после обрыва
по бюджету (`timeout` шлёт SIGTERM, `atexit` не идёт), и после SIGKILL: вывод mutmut гейт
складывает в файл и на зелёном не показывает. Без mutmut (`MUTANT_UNDER_TEST` пуст) молчит.
"""

from __future__ import annotations

import itertools
import os
import statistics
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

KEYS = ("configure", "start", "prepared", "loop", "loop_end", "unconfigure")
#: Фазы главного процесса mutmut; остальные значения `MUTANT_UNDER_TEST` — имена мутантов.
MAIN_PHASES = {"stats", "fail", "list_all_tests", "mutant_generation"}
MARKS: dict[str, float] = {}


def phase() -> str:
    return os.environ.get("MUTANT_UNDER_TEST", "")


def journal(main: int) -> Path:
    return Path(tempfile.gettempdir()) / f"mutation-timing-{main}.tsv"


def main_pid() -> int:
    """Ребёнок mutmut — форк главного процесса: главный — его родитель."""
    return os.getpid() if phase() in MAIN_PHASES else os.getppid()


def mark(name: str) -> None:
    if phase():
        MARKS[name] = time.time()


def pytest_configure(config: pytest.Config) -> None:
    MARKS.clear()
    mark("configure")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtestloop(session: pytest.Session) -> Iterator[None]:
    mark("loop")
    yield
    mark("loop_end")


def pytest_unconfigure(config: pytest.Config) -> None:
    if not phase():
        return
    mark("unconfigure")
    row = "\t".join([str(os.getpid()), phase(), *(f"{MARKS.get(k, 0.0):.4f}" for k in KEYS)])
    with journal(main_pid()).open("a", encoding="utf-8") as out:
        out.write(row + "\n")


def part(rows: list[dict[str, float]], end: str, begin: str) -> list[float]:
    return [r[end] - r[begin] for r in rows if r[end] and r[begin]]


def line(name: str, values: list[float]) -> str:
    if not values:
        return f"  {name}: нет данных"
    return (
        f"  {name}: среднее {statistics.mean(values):.3f} с,"
        f" медиана {statistics.median(values):.3f} с, сумма {sum(values):.1f} с"
    )


def summary(path: Path) -> None:
    rows, main = [], []
    for raw in path.read_text(encoding="utf-8").splitlines():
        _, name, *marks = raw.split("\t")
        row = dict(zip(KEYS, map(float, marks), strict=True))
        (main if name in MAIN_PHASES else rows).append((name, row))
    print(f"проба хода 0: {path.name} — сессий порч {len(rows)}, главного процесса {len(main)}")
    for name, row in main:
        print(f"  главный процесс, {name}: {row['unconfigure'] - row['configure']:.1f} с")
    sessions = sorted((row for _, row in rows), key=lambda r: r["configure"])
    gaps = [b["configure"] - a["unconfigure"] for a, b in itertools.pairwise(sessions)]
    for name, end, begin in (
        ("запуск pytest до сессии", "start", "configure"),
        ("подготовка базы", "prepared", "start"),
        ("сборка тестов", "loop", "prepared"),
        ("тесты", "loop_end", "loop"),
        ("конец сессии", "unconfigure", "loop_end"),
        ("вся сессия", "unconfigure", "configure"),
    ):
        print(line(name, part(sessions, end, begin)))
    print(line("между сессиями (fork, mutmut, pytest до настройки)", gaps))
    if sessions:
        wall = sessions[-1]["unconfigure"] - sessions[0]["configure"]
        print(f"  фаза порч по стене: {wall:.1f} с, на порчу {wall / len(sessions):.3f} с")


if __name__ == "__main__":
    found = sorted(Path(tempfile.gettempdir()).glob("mutation-timing-*.tsv"))
    if not found:
        print(f"проба хода 0: журналов в {tempfile.gettempdir()} нет")
        sys.exit(1)
    for path in found:
        summary(path)
