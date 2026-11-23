"""Проба хода 0 «Скорости CI» (A280) — только в ветке-пробе, в main не идёт.

Каждая сессия pytest под mutmut пишет строку отметок времени в журнал главного процесса mutmut:
начало настройки, начало сессии, база готова, начало и конец тестов, конец настройки. `atexit`
главного процесса печатает сводку частей одной порчи. Без mutmut (`MUTANT_UNDER_TEST` пуст) молчит.
Дети mutmut выходят через `os._exit` и `atexit` не зовут.
"""

from __future__ import annotations

import atexit
import itertools
import os
import statistics
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

KEYS = ("configure", "start", "prepared", "loop", "loop_end", "unconfigure")
#: Фазы главного процесса mutmut; остальные значения `MUTANT_UNDER_TEST` — имена мутантов.
MAIN_PHASES = {"stats", "fail", "list_all_tests", "mutant_generation"}
MARKS: dict[str, float] = {}
REGISTERED: list[int] = []


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
    main = main_pid()
    row = "\t".join([str(os.getpid()), phase(), *(f"{MARKS.get(k, 0.0):.4f}" for k in KEYS)])
    with journal(main).open("a", encoding="utf-8") as out:
        out.write(row + "\n")
    if main == os.getpid() and not REGISTERED:
        REGISTERED.append(main)
        atexit.register(summary, main)


def part(rows: list[dict[str, float]], end: str, begin: str) -> list[float]:
    return [r[end] - r[begin] for r in rows if r[end] and r[begin]]


def line(name: str, values: list[float]) -> str:
    if not values:
        return f"  {name}: нет данных"
    return (
        f"  {name}: среднее {statistics.mean(values):.3f} с,"
        f" медиана {statistics.median(values):.3f} с, сумма {sum(values):.1f} с"
    )


def summary(main: int) -> None:
    rows, stats = [], []
    for raw in journal(main).read_text(encoding="utf-8").splitlines():
        _pid, name, *marks = raw.split("\t")
        row = dict(zip(KEYS, map(float, marks), strict=True))
        (stats if name in MAIN_PHASES else rows).append(row)
    rows.sort(key=lambda r: r["configure"])
    gaps = [b["configure"] - a["unconfigure"] for a, b in itertools.pairwise(rows)]
    print(f"проба хода 0: сессий порч {len(rows)}, сессий главного процесса {len(stats)}")
    print(line("статистика (весь набор)", part(stats, "unconfigure", "configure")))
    for name, end, begin in (
        ("запуск pytest до сессии", "start", "configure"),
        ("подготовка базы", "prepared", "start"),
        ("сборка тестов", "loop", "prepared"),
        ("тесты", "loop_end", "loop"),
        ("конец сессии", "unconfigure", "loop_end"),
        ("вся сессия", "unconfigure", "configure"),
    ):
        print(line(name, part(rows, end, begin)))
    print(line("между сессиями (fork, mutmut, pytest до настройки)", gaps))
