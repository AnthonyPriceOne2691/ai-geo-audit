"""Выдуманный справочник тестов — Ф и Ф2 спеки Ч2.1: файл, манифест к нему и помощник сырых
вставок редакции.

Коды — 90–99: в настоящем справочнике таких нет (A303). Тексты — слова-заглушки. Редакции до
записи под тестом ставит помощник сырыми вставками, мимо кода сервиса.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
import dbtools

from ai_geo_audit.reference.file import UNITS
from ai_geo_audit.reference.models import Unit

#: Дата снимка Ф — как в примерах спеки.
SNAPSHOT = date(2026, 11, 18)
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
    positions: Sequence[int] | None = None,
) -> int:
    """Редакция сырыми вставками: заголовок, строки по месту с 1 (`positions` — свои места, вставка
    не по их порядку), пороги с пределами единицы. У файла sha256 и дата снимка выдуманные, у
    правки их нет; архивная — с `archived_at`. Возвращает id редакции."""
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
    for position, item in zip(positions or range(1, len(rows) + 1), rows, strict=True):
        check = await conn.fetchval(_CHECK, revision, position, *(item[f] for f in FIELDS))
        for param in item["params"]:
            low, high = UNITS[Unit(param["unit"])]
            values = (param["name"], param["value"], low, high, param["unit"])
            await conn.execute(_PARAM, check, *values)
    return revision


async def _insert_all(items: Sequence[Mapping[str, Any]]) -> list[tuple[int, str]]:
    conn = await asyncpg.connect(dbtools._raw(dbtools.own_url()))
    try:
        based_on = None
        for item in items:
            based_on = await insert_revision(conn, **item, based_on=based_on)
        found = await conn.fetch("SELECT number, status FROM reference_revisions ORDER BY number")
        return [(row["number"], row["status"]) for row in found]
    finally:
        await conn.close()


def revisions(*items: Mapping[str, Any]) -> list[tuple[int, str]]:
    """Редакции `items` по порядку — каждая на основе прежней — сырыми вставками в тестовую базу;
    затем (номер, статус) всех редакций."""
    return asyncio.run(_insert_all(items))


async def _empty() -> None:
    conn = await asyncpg.connect(dbtools._raw(dbtools.own_url()))
    try:
        await conn.execute("TRUNCATE reference_params, reference_checks, reference_revisions")
    finally:
        await conn.close()


def empty() -> None:
    """Таблицы справочника тестовой базы пусты."""
    asyncio.run(_empty())


def document(rows: Sequence[Mapping[str, Any]] = SAMPLE, **top: Any) -> dict[str, Any]:
    """Документ формата 1: строки (по умолчанию Ф), снимок Ф; правки верхнего уровня — `top`."""
    return {"format": 1, "source_date": SNAPSHOT.isoformat(), "rows": list(rows)} | top


def _number(value: object) -> int | float:
    """`Decimal` в тексте JSON — числом: целое без точки, дробное как есть."""
    if not isinstance(value, Decimal):
        raise TypeError(type(value).__name__)
    return int(value) if value == value.to_integral_value() else float(value)


def dump(doc: object) -> bytes:
    """Текст JSON в UTF-8, по полю на строку; кириллица — как есть."""
    return json.dumps(doc, ensure_ascii=False, indent=1, default=_number).encode()


def manifest_of(data: bytes, rows: int, **changes: Any) -> dict[str, Any]:
    """Манифест байтов файла: формат 1, число строк, sha256, дата снимка; правки — `changes`."""
    sha = hashlib.sha256(data).hexdigest()
    return {"format": 1, "rows": rows, "sha256": sha, "source_date": SNAPSHOT.isoformat()} | changes


def files(tmp_path: Path, data: bytes, manifest: Mapping[str, Any]) -> tuple[str, str]:
    """Файл и манифест во временном каталоге теста — пути строками для аргументов команды."""
    path, spec = tmp_path / "reference.json", tmp_path / "manifest.json"
    path.write_bytes(data)
    spec.write_text(json.dumps(manifest), encoding="utf-8")
    return str(path), str(spec)
