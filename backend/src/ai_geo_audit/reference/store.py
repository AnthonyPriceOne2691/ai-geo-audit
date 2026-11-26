"""Чтение справочника (A304, A310, A312): активная редакция, её строки, «было — станет».

`diff` — одна функция у `check`, записи и правки Ч2.1б и экрана Ч2.4 (Р94): строки редакции и
файла сравниваются одним видом `RowIn`; тексты называются длиной, без самих текстов (A307); числа
сравниваются значением `Decimal`, а не записью: `3.25` и `3.250` — одно число (L131).
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_geo_audit.reference.file import ParamIn, RowIn
from ai_geo_audit.reference.models import (
    UNIT_WORDS,
    WORDS,
    ReferenceCheck,
    ReferenceParam,
    ReferenceRevision,
    RevisionSource,
    RevisionStatus,
)

NOT_LOADED: Final = "справочник не загружен — загрузка: python -m ai_geo_audit.reference"
_FIELDS: Final = tuple(name for name in RowIn.model_fields if name != "params")
_TEXTS: Final = {
    "block": "блок",
    "name": "имя",
    "norm": "норма",
    "error": "ошибка",
    "recommendation": "рекомендация",
}
_ENUMS: Final = {
    "method": "способ",
    "coverage": "охват",
    "importance": "важность",
    "scope": "область",
}


class NotLoadedError(LookupError):
    """Активной редакции нет: проверки не считаются по умолчаниям."""


async def active_revision(session: AsyncSession) -> ReferenceRevision:
    """Активная редакция; нет её — отказ «справочник не загружен» (A310)."""
    query = select(ReferenceRevision).where(ReferenceRevision.status == RevisionStatus.ACTIVE)
    found = await session.scalar(query)
    if found is None:
        raise NotLoadedError(NOT_LOADED)
    return found


async def last_file_revision(session: AsyncSession, number: int) -> ReferenceRevision | None:
    """Последняя редакция из файла с номером не больше `number`: от неё считаются правки."""
    query = select(ReferenceRevision).where(
        ReferenceRevision.source == RevisionSource.FILE, ReferenceRevision.number <= number
    )
    return await session.scalar(query.order_by(ReferenceRevision.number.desc()).limit(1))


async def revision_rows(session: AsyncSession, revision_id: int) -> list[RowIn]:
    """Строки редакции по месту — тем же видом, что строки файла."""
    query = select(ReferenceCheck).where(ReferenceCheck.revision_id == revision_id)
    checks = (await session.scalars(query.order_by(ReferenceCheck.position))).all()
    owned = select(ReferenceParam).where(ReferenceParam.check_id.in_([c.id for c in checks]))
    params: dict[int, list[ParamIn]] = {}
    for param in (await session.scalars(owned.order_by(ReferenceParam.id))).all():
        found = ParamIn(name=param.name, value=param.value, unit=param.unit)
        params.setdefault(param.check_id, []).append(found)
    return [
        RowIn(
            **{field: getattr(check, field) for field in _FIELDS}, params=params.get(check.id, [])
        )
        for check in checks
    ]


def missing(old: Sequence[RowIn], new: Sequence[RowIn]) -> list[str]:
    """Коды `old`, которых нет в `new`, по порядку `old` (A312)."""
    codes = {row.code for row in new}
    return [row.code for row in old if row.code not in codes]


def diff(old: Sequence[RowIn], new: Sequence[RowIn]) -> list[tuple[str, str]]:
    """«Было — станет» из `old` в `new` по строкам `new`: пары «код:поле» и строка вывода."""
    before = {row.code: row for row in old}
    found: list[tuple[str, str]] = []
    for row in new:
        was = before.get(row.code)
        found += _changes(was, row) if was else [(f"{row.code}:", f"новая строка {row.code}")]
    return found


def _number(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _field(field: str, old: str, new: str) -> str:
    """Смена поля строки: текст — длиной, без текста; перечисление — словом."""
    if field in _TEXTS:
        return f"текст изменён, было {len(old)} знаков, стало {len(new)}"
    return f"{WORDS.get(old, old)} → {WORDS.get(new, new)}"


def _changes(was: RowIn, now: RowIn) -> list[tuple[str, str]]:
    """Поля строки, доля и пороги — в этом порядке."""
    code = now.code
    found = [
        (
            f"{code}:{field}",
            f"{code} · {word}: {_field(field, getattr(was, field), getattr(now, field))}",
        )
        for field, word in (_TEXTS | _ENUMS).items()
        if getattr(was, field) != getattr(now, field)
    ]
    if was.share_percent != now.share_percent:
        old, new = (
            "—" if s is None else f"{_number(s)} %" for s in (was.share_percent, now.share_percent)
        )
        found.append((f"{code}:share_percent", f"{code} · доля: {old} → {new}"))
    return found + _params(code, was.params, now.params)


def _params(code: str, was: Sequence[ParamIn], now: Sequence[ParamIn]) -> list[tuple[str, str]]:
    """Пороги: новый, убранный, другое значение или единица — значением, а не записью."""
    before, after = {p.name: p for p in was}, {p.name: p for p in now}
    found = []
    for name in sorted(before.keys() | after.keys()):
        old, new = before.get(name), after.get(name)
        if new is None:
            found.append((f"{code}:param:{name}", f"{code} · порог {name} убран"))
        elif old is None or (old.value, old.unit) != (new.value, new.unit):
            start = f"{_number(old.value)} → " if old else "новый, "
            line = f"{code} · {name} {start}{_number(new.value)} {UNIT_WORDS[new.unit]}"
            found.append((f"{code}:param:{name}", line.rstrip()))
    return found
