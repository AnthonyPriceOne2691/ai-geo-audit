"""Выдуманный справочник тестов — Ф и Ф2 спеки Ч2.1 — и помощник сырых вставок редакции.

Коды — 90–99: в настоящем справочнике таких нет (A303). Тексты — слова-заглушки. Записи командой
до Ч2.1б нет: редакции тестов ставит помощник сырыми вставками, мимо кода сервиса.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import asyncpg

#: Дата снимка Ф — как в примерах спеки.
SNAPSHOT = date(2026, 11, 18)
#: Пределы единицы порога Ф: `s` — 0.1–60, как в таблице единиц спеки.
BOUNDS = {"s": (Decimal("0.1"), Decimal("60"))}
#: Поля строки справочника в порядке колонок `reference_checks` после места.
FIELDS = (
    "code",
    "block",
    "name",
    "method",
    "coverage",
    "norm",
    "error",
    "recommendation",
    "importance",
    "scope",
    "share_percent",
)


def row(code: str, **changes: Any) -> dict[str, Any]:
    """Строка Ф: «все страницы», доля 10 %, «Важно», тексты-заглушки с кодом."""
    base: dict[str, Any] = {
        "code": code,
        "block": f"блок {code}",
        "name": f"проверка {code}",
        "method": "rules",
        "coverage": "full",
        "norm": f"норма {code}",
        "error": f"ошибка {code}",
        "recommendation": f"рекомендация {code}",
        "importance": "important",
        "scope": "pages",
        "share_percent": Decimal("10"),
        "params": [],
    }
    return base | changes


#: Ф: `91` — «сайт», «Критично»; `92` — порог `max_seconds` 3.25 с; `92.1` — выборка, доля 30 %;
#: `94.2` — «Нет данных».
SAMPLE = [
    row("91", importance="critical", scope="site", share_percent=None),
    row("92", params=[{"name": "max_seconds", "value": Decimal("3.25"), "unit": "s"}]),
    row("92.1", scope="sample", share_percent=Decimal("30")),
    row("93"),
    row("94"),
    row("94.2", coverage="no_data"),
]
#: Ф2: у `92` доля 15, у `93` другая норма и новая строка `95`.
CHANGED: dict[str, dict[str, Any]] = {
    "92": {"share_percent": Decimal("15")},
    "93": {"norm": "другая норма 93"},
}
SAMPLE2 = [item | CHANGED.get(item["code"], {}) for item in SAMPLE] + [row("95")]

_REVISION = (
    "INSERT INTO reference_revisions (number, status, source, based_on_id, file_sha256,"
    " file_rows, source_date, reason, archived_at)"
    " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) RETURNING id"
)
_CHECK = (
    "INSERT INTO reference_checks (revision_id, position, code, block, name, method, coverage,"
    " norm, error, recommendation, importance, scope, share_percent)"
    " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13) RETURNING id"
)
_PARAM = (
    "INSERT INTO reference_params (check_id, name, value, min_value, max_value, unit)"
    " VALUES ($1, $2, $3, $4, $5, $6)"
)


async def insert_revision(
    conn: asyncpg.Connection,
    rows: Sequence[Mapping[str, Any]],
    *,
    number: int,
    status: str = "active",
    source: str = "file",
    based_on: int | None = None,
) -> int:
    """Редакция сырыми вставками: заголовок, строки по месту с 1, пороги с пределами единицы. У
    файла sha256 и дата снимка выдуманные, у правки их нет; архивная — с `archived_at`. Возвращает
    id редакции."""
    from_file = source == "file"
    revision: int = await conn.fetchval(
        _REVISION,
        number,
        status,
        source,
        based_on,
        hashlib.sha256(f"файл {number}".encode()).hexdigest() if from_file else None,
        len(rows) if from_file else None,
        SNAPSHOT if from_file else None,
        f"проба {number}" if from_file else "",
        datetime.now(UTC) if status == "archived" else None,
    )
    for position, item in enumerate(rows, 1):
        check = await conn.fetchval(_CHECK, revision, position, *(item[f] for f in FIELDS))
        for param in item["params"]:
            low, high = BOUNDS[param["unit"]]
            values = (param["name"], param["value"], low, high, param["unit"])
            await conn.execute(_PARAM, check, *values)
    return revision
