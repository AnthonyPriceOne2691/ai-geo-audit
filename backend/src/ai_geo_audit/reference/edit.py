"""Правка порогов, доли и важности новой редакцией (Ч2.1б: A315–A319) — функция для маршрута Ч2.3
и экрана Ч2.4: права, журнал действий и HTTP — там.

Правка — `(код, поле, значение)`; поле — `importance`, `share_percent` или `param:<имя>`, тексты и
прочие поля строки — отказ: тексты меняет инженер файлом (ТЗ:189). Значение доли и порога —
только `Decimal` и не NaN, до сравнения с пределом: NaN в сравнении бросает `InvalidOperation`
(L131; decisions, проба 2026-11-27T08:48:51Z); бесконечность — вне пределов. Не больше 6 знаков
после точки и 15 значащих — тоже до предела: иначе отчёт округлит значение, а `numeric` его не
запишет (decisions, сверка среза 2). Предел порога — из редакции, в новую он переходит тем же. Все
причины — за один вызов, в порядке правок. Отчёт — строки `diff` Ч2.1 в порядке правок и «редакция
N → N+1» — один у предпросмотра и у записи (Р94). Запись — `store.write_revision`: замок, активная
после него, сверка `expected` под ним (A319)."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Final, TypeGuard

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ai_geo_audit.reference import store
from ai_geo_audit.reference.file import FileRefusedError, RowIn
from ai_geo_audit.reference.models import (
    UNIT_WORDS,
    Importance,
    ReferenceCheck,
    ReferenceParam,
    ReferenceRevision,
    RevisionSource,
    Scope,
)

#: Правка: код строки, поле, новое значение.
Change = tuple[str, str, object]
STALE: Final = "справочник уже изменён: вы смотрели {}, активна {} — перечитайте"
NOTHING: Final = "правок нет — ничего не меняется"
TEXTS: Final = "тексты строки этой функцией не правятся"
PLACES: Final = 6
SIGNIFICANT: Final = 15
TOO_FINE: Final = f"не больше {PLACES} знаков после точки и {SIGNIFICANT} значащих"
_TEXT_FIELDS: Final = frozenset({"block", "name", "norm", "error", "recommendation"})
_IMPORTANCE: Final = frozenset(Importance)
_WORDS: Final = {"importance": "важность", "share_percent": "доля"}


async def change_thresholds(
    session: AsyncSession, *, expected: int, changes: Sequence[Change], apply: bool
) -> list[str]:
    """Отчёт правки поверх активной редакции `expected`; `apply=True` — она же записана новой
    редакцией: источник «правка», основа — `expected`, причина пустая (кто правил — журнал Ч2.3).
    Отказ — `FileRefusedError` со всеми причинами, база не тронута. Сессия — своя у правки, до
    вызова в ней не пишут: запись её фиксирует, отказ и сбой записи откатывают; предпросмотр только
    читает."""
    report: list[str] = []

    async def decide(session: AsyncSession, active: ReferenceRevision | None) -> store.NewRevision:
        if active is None:
            raise FileRefusedError([store.NOT_LOADED])
        if active.number != expected:
            raise FileRefusedError([STALE.format(expected, active.number)])
        rows = await store.revision_rows(session, active.id)
        bounds = await _bounds(session, active.id)
        edited, why = _edited(active.number, rows, bounds, changes)
        if why:
            raise FileRefusedError(why)
        lines = dict(store.diff(rows, edited))
        report[:] = [lines[_key(code, field)] for code, field, _ in changes]
        report.append(f"редакция {active.number} → {active.number + 1}")
        return store.NewRevision(edited, RevisionSource.EDIT, "", bounds=bounds)

    if apply:
        await store.write_revision(session, decide)
    else:
        await decide(session, await store.active_or_none(session))
    return report


async def _bounds(
    session: AsyncSession, revision_id: int
) -> dict[tuple[str, str], tuple[Decimal, Decimal]]:
    """Пределы порогов редакции по (код, имя) — те, что легли в неё при записи."""
    query = (
        select(
            ReferenceCheck.code,
            ReferenceParam.name,
            ReferenceParam.min_value,
            ReferenceParam.max_value,
        )
        .join(ReferenceParam, ReferenceParam.check_id == ReferenceCheck.id)
        .where(ReferenceCheck.revision_id == revision_id)
    )
    return {(code, name): (low, high) for code, name, low, high in await session.execute(query)}


def _key(code: str, field: str) -> str:
    """Ключ строки `diff` по правке: `92:param:max_seconds`, `91:importance`."""
    return f"{code}:{field}"


def _edited(
    number: int, rows: Sequence[RowIn], bounds: store.Bounds, changes: Sequence[Change]
) -> tuple[list[RowIn], list[str]]:
    """Строки редакции с правками и причины отказа — по одной на негодную правку, по порядку."""
    found = {row.code: row for row in rows}
    why = [] if changes else [NOTHING]
    seen: set[tuple[str, str]] = set()
    for code, field, value in changes:
        row = found.get(code)
        if row is None:
            why.append(f"кода {code} нет в редакции {number}")
            continue
        if (code, field) in seen:
            why.append(f"{code} · {_word(field)}: правка повторяется")
            continue
        seen.add((code, field))
        problem = _problem(row, field, value, bounds)
        if problem is not None:
            why.append(problem)
        else:
            found[code] = _changed(row, field, value)
    return [found[row.code] for row in rows], why


def _word(field: str) -> str:
    return _WORDS.get(field) or field.removeprefix("param:")


def _problem(row: RowIn, field: str, value: object, bounds: store.Bounds) -> str | None:
    """Что не так с правкой поля строки; годна — None."""
    if field == "importance":
        return _importance_problem(row, value)
    if field == "share_percent":
        return _share_problem(row, value)
    if field.startswith("param:"):
        return _param_problem(row, _word(field), value, bounds)
    return TEXTS if field in _TEXT_FIELDS else f"поле {field} этой функцией не правится"


def _importance_problem(row: RowIn, value: object) -> str | None:
    if not (isinstance(value, str) and value in _IMPORTANCE):
        return f"{row.code} · важность — Критично, Важно или Низкий приоритет"
    return _same(row.code, _WORDS["importance"], row.importance == value)


def _share_problem(row: RowIn, value: object) -> str | None:
    if row.scope is Scope.SITE:
        return f"{row.code} · у проверки по сайту доли нет"
    if not _exact(value):
        return f"{row.code} · доля — нужно точное число"
    if _too_fine(value):
        return f"{row.code} · доля — {TOO_FINE}"
    if not Decimal(0) < value <= 100:
        return f"{row.code} · доля — больше 0 и не больше 100"
    return _same(row.code, _WORDS["share_percent"], row.share_percent == value)


def _param_problem(row: RowIn, name: str, value: object, bounds: store.Bounds) -> str | None:
    params = {param.name: param for param in row.params}
    if name not in params:
        return f"{row.code}: порога {name} у строки нет"
    if not _exact(value):
        return f"{row.code} · {name} — нужно точное число"
    if _too_fine(value):
        return f"{row.code} · {name} — {TOO_FINE}"
    low, high = bounds[(row.code, name)]
    if not low <= value <= high:
        unit = UNIT_WORDS[params[name].unit]
        span = f"{_number(low)}–{_number(high)} {unit}".rstrip()
        return f"{row.code} · {name}: {_number(value)} вне пределов {span}"
    return _same(row.code, name, params[name].value == value)


def _exact(value: object) -> TypeGuard[Decimal]:
    """Точное число: `Decimal` и не NaN; булево, `float`, `int` и строка — нет (L131)."""
    return type(value) is Decimal and not value.is_nan()


def _too_fine(value: Decimal) -> bool:
    """Знаков после точки больше `PLACES` или значащих больше `SIGNIFICANT` — по цифрам записи
    без хвостовых нулей, без округления контекста (`normalize` округлил бы до 28 знаков);
    бесконечность и ноль — не тоньше: бесконечность судит предел."""
    _, digits, exponent = value.as_tuple()
    kept = "".join(map(str, digits)).rstrip("0")
    if not isinstance(exponent, int) or not kept:
        return False
    last = exponent + len(digits) - len(kept)
    return -last > PLACES or len(kept) > SIGNIFICANT


def _same(code: str, word: str, same: bool) -> str | None:
    return f"{code} · {word}: значение то же — ничего не меняется" if same else None


def _number(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _changed(row: RowIn, field: str, value: object) -> RowIn:
    """Строка с годной правкой: важность, доля или значение порога."""
    if field == "importance":
        return row.model_copy(update={"importance": Importance(str(value))})
    if field == "share_percent":
        return row.model_copy(update={"share_percent": value})
    name = field.removeprefix("param:")
    params = [p.model_copy(update={"value": value}) if p.name == name else p for p in row.params]
    return row.model_copy(update={"params": params})
