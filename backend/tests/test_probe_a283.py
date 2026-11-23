"""Проба A283 «Скорости CI» — только в ветке-пробе, в main не идёт.

Ребёнок mutmut в обычном прогоне гейта (область — глобом) пишет строку в таблицу проб своей базы,
ждёт живого соседа — другого ребёнка того же прогона — до 5 с и считает строки своей таблицы:
сосед пишет свою строку в свою копию, своя таблица видит одну. Итог сессии — строкой в журнал
прогона во временной папке сразу после счёта, до задержки для соседей; соседа не было — строка
«соседа не было», без задержки, и красное. Ожидание короче предела mutmut 3.8.1 по стене:
(оценка тестов + 1) × 15 с, оценка из статистики — сотые доли секунды. С ожиданием 30 с контроль
в 1 поток погибал по таймауту до строки итога (прогон 45159003562, все семь ⏰). Прогон
самопроверки (порчи по именам, по одной) протокол не гоняет. Вне ребёнка — статистика, clean
tests, forced fail, обычный прогон — только функция области.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

import asyncpg
import dbtools

from ai_geo_audit import probe_a283

WAIT = 5
LINGER = 3


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def neighbour(folder: Path, me: int) -> int | None:
    """Живой сосед — другой ребёнок с отметкой в папке прогона; ждать не дольше `WAIT`."""
    deadline = time.monotonic() + WAIT
    while time.monotonic() < deadline:
        for mark in folder.glob("*.alive"):
            pid = int(mark.stem)
            if pid != me and alive(pid):
                return pid
        time.sleep(0.2)
    return None


def value(sql: str, *args: object) -> object:
    """Один запрос к своей базе сессии — копии шаблона у ребёнка."""

    async def go() -> object:
        conn = await asyncpg.connect(dbtools._raw(dbtools.own_url()))
        try:
            return await conn.fetchval(sql, *args)
        finally:
            await conn.close()

    return asyncio.run(go())


def test_a283_probe_copy_sees_only_its_own_row() -> None:
    """A283: проба-порча с соседом — строка соседа в своей копии не видна; соседа не было — красное
    «соседа не было»."""
    label = os.environ.get("MUTANT_UNDER_TEST", "")
    regular = any("*" in arg for arg in sys.argv)
    if "__mutmut_" in label and regular:
        folder = Path(tempfile.gettempdir()) / f"a283-{os.getppid()}"
        folder.mkdir(exist_ok=True)
        me, key = os.getpid(), f"a283-{os.getpid()}"
        mark = folder / f"{me}.alive"
        value("INSERT INTO probe.probe (key) VALUES ($1) RETURNING id", key)
        mark.write_text(label, encoding="utf-8")
        try:
            found = neighbour(folder, me)
            rows = value("SELECT count(*) FROM probe.probe")
            seen = f"сосед {found}" if found else "соседа не было"
            with (folder / "results.log").open("a", encoding="utf-8") as out:
                out.write(f"{label}\t{seen}\tстрок в своей копии {rows}\n")
            if found:
                time.sleep(LINGER)  # сосед, пришедший позже, застанет живым
        finally:
            mark.unlink(missing_ok=True)
        value("DELETE FROM probe.probe WHERE key = $1 RETURNING id", key)
        assert found, "соседа не было"
        assert rows == 1, f"строк в своей копии {rows}: сосед пишет в ту же базу"
    assert probe_a283.a283(3, 4) == 20
