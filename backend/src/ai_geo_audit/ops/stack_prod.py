"""Боевые правила стека (A89): лимиты памяти и процессора у каждого сервиса, журнал journald с
меткой сервиса и потолок Redis без вытеснения. Включаются флагом `--prod` правил стека над слитым
выводом основного файла с боевым наложением; перечень, порядок и вывод — в `stack_rules`: там
коды 25–27 ведут сюда.

Вход — вывод `--no-interpolate`: значения стоят как в файле — `mem_limit` строкой (`1g`), `cpus`
числом или строкой, `command` списком или строкой (замер compose 5.7.0, decisions Ч1.5в), у redis —
`sh -c` со строкой и here-doc («Учётки хранилищ», проба 3 среза 0 на compose 5.7.1).
Модуль берёт `Stack` правил стека только для типов, а класс отказа — из `stack_refusal`, поэтому
`stack_rules` импортирует отсюда без круга.
"""

from __future__ import annotations

import itertools
import shlex
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Any

from ai_geo_audit.ops.stack_refusal import InputRefusedError

if TYPE_CHECKING:
    from ai_geo_audit.ops.stack_rules import Finding, Stack

#: Метка журнала сервиса — имя стека и сервиса, `ai-geo-audit-api` (decisions Ч1.5в, срез 0).
TAG = "ai-geo-audit-{service}"
#: Лимита нет: нет ключа, пусто или ноль.
UNLIMITED = (None, "", 0, "0")
#: Потолок Redis — `--maxmemory` не ноль и вытеснение явной строкой `noeviction`.
POLICY = "noeviction"


def _unknown(name: str, what: str, values: tuple[object, ...]) -> None:
    """Значение ссылкой `${…}` — на сервере неизвестно: отказ, а не «нарушений нет»."""
    if any("${" in str(value) for value in values):
        why = f"сервис {name}: {what} — ссылка ${{…}}, значение на сервере неизвестно"
        raise InputRefusedError(why)


def limits(stack: Stack) -> Iterator[Finding]:
    for name, service in stack.services.items():
        for key in ("mem_limit", "cpus"):
            _unknown(name, key, (service.get(key),))
        missing = [key for key in ("mem_limit", "cpus") if service.get(key) in UNLIMITED]
        if missing:
            yield name, None, f"нет {' и '.join(missing)}"


def journald(stack: Stack) -> Iterator[Finding]:
    for name, service in stack.services.items():
        journal = service.get("logging") or {}
        tag = TAG.format(service=name)
        if journal.get("driver") != "journald" or (journal.get("options") or {}).get("tag") != tag:
            yield name, None, f"журнал не journald с меткой {tag}"


def _words(text: str) -> list[str]:
    """Слова строки по разбору оболочки, как у compose; не делится (незакрытая кавычка) — отказ."""
    try:
        return shlex.split(text)
    except ValueError:
        raise InputRefusedError("сервис redis: command не делится на слова оболочки") from None


def _command(service: dict[str, Any]) -> list[str]:
    """Команда сервиса словами: строкой — как в файле, разбор оболочки; списком — как есть.
    `sh -c` со строкой — слова её первой строки до here-doc: так «Учётки хранилищ» дают redis
    пароль на входе, а флаги боевого наложения ставят после `-` (A171, A89)."""
    raw = service.get("command") or []
    words = _words(raw) if isinstance(raw, str) else [str(word) for word in raw]
    if len(words) == 3 and words[:2] == ["sh", "-c"]:
        return _words(words[2].split("\n", 1)[0].split("<<", 1)[0])
    return words


def redis_memory(stack: Stack) -> Iterator[Finding]:
    """Судит только два своих флага; ссылки в других словах команды — не отказ. Пароль очереди
    «Учёток хранилищ» — на входе here-doc, в слова первой строки он не попадает."""
    if "redis" not in stack.services:
        return
    after = dict(itertools.pairwise(_command(stack.services["redis"])))
    flags = ("--maxmemory", "--maxmemory-policy")
    _unknown("redis", " или ".join(flags), tuple(after.get(flag) for flag in flags))
    if after.get("--maxmemory") in (None, "0") or after.get("--maxmemory-policy") != POLICY:
        yield "redis", None, f"нет --maxmemory или вытеснение не {POLICY}"


CHECKS: tuple[tuple[str, Callable[[Stack], Iterator[Finding]]], ...] = (
    ("limits", limits),
    ("journald", journald),
    ("redis-memory", redis_memory),
)
