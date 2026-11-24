"""Разбор остановки контейнера api (A88): по коду выхода, записи uvicorn о конце процесса и
команде остановки — «остановлен», «убит по сроку» или «упал».

uvicorn 0.54 после мягкой остановки снова поднимает SIGTERM, и init отдаёт докеру смерть от
сигнала: код 143 (128 + 15) после команды остановки — норма, а не падение. Код 137 (128 + 9)
после команды — докер убил процесс по истечении срока: мягкая остановка не уложилась. Разбор
один на оракул (A88) и сторожа Ч1.14.
"""

from __future__ import annotations

from enum import StrEnum

#: Коды нормальной остановки: выход 0 и смерть от SIGTERM.
STOP_CODES = frozenset({0, 143})
#: Смерть от SIGKILL: докер убил процесс по истечении `stop_grace_period`.
KILL_CODE = 137


class Verdict(StrEnum):
    """Вердикт остановки словами примера A88."""

    STOPPED = "остановлен"
    KILLED = "убит по сроку"
    CRASHED = "упал"


def verdict(code: int, *, finished: bool, stopped: bool) -> Verdict:
    """Вердикт остановки: `finished` — в журнале есть запись uvicorn «Finished server process»,
    `stopped` — была команда остановки. Всё, что не «остановлен» и не «убит по сроку», — «упал»."""
    if stopped and code == KILL_CODE:
        return Verdict.KILLED
    if stopped and finished and code in STOP_CODES:
        return Verdict.STOPPED
    return Verdict.CRASHED
