"""Справочник в базе: чтение (A304, A310, A312) — активная редакция, её строки, «было — станет»;
запись новой редакции (Ч2.1б: A311, A313, A314).

`diff` — одна функция у `check`, записи и правки Ч2.1б и экрана Ч2.4 (Р94): строки редакции и
файла сравниваются одним видом `RowIn`; тексты называются длиной, без самих текстов (A307); числа
сравниваются значением `Decimal`, а не записью: `3.25` и `3.250` — одно число (L131).

Запись — одна функция у загрузки и правки (Р94): одна транзакция, первыми запросами — предел
ожидания и замок `REFERENCE`, как у миграций (`env.py`); активная читается после замка (decisions,
проба 2). Фиксация — явная, в своей ветке: сбой до неё — «справочник не тронут», на ней — «запись
могла пройти» (decisions, проба 4).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from sqlalchemy import func, insert, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from ai_geo_audit.reference.file import UNITS, FileRefusedError, ParamIn, ReferenceFile, RowIn
from ai_geo_audit.reference.models import (
    UNIT_WORDS,
    WORDS,
    ReferenceCheck,
    ReferenceParam,
    ReferenceRevision,
    RevisionSource,
    RevisionStatus,
)
from ai_geo_audit.storage import locks

NOT_LOADED: Final = "справочник не загружен — загрузка: python -m ai_geo_audit.reference"
#: Предел ожидания замка записи, с — константа без настройки: правка — через ревью (decisions).
LOCK_WAIT_SECONDS = 10
BUSY: Final = "справочник сейчас пишет другая запись: ждали {} с — повторите позже"
FILE_CHANGED: Final = "файл изменился после проверки — повторите check"
CHANGED: Final = "справочник изменился после проверки: была {}, активна {} — повторите check"
EDITED: Final = (
    "после файла справочник правили: редакция {} — добавьте --replace-edits, если правку нужно"
    " отменить"
)
GONE: Final = (
    "строки {} нет в файле: строки справочника не удаляются — верните её в файл; снять проверку с"
    " аудитов — убрать её из наборов"
)
MAYBE_WRITTEN: Final = "запись могла пройти, проверьте status"
_LOCK_NOT_AVAILABLE: Final = "55P03"
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


def missing_reasons(old: Sequence[RowIn], new: Sequence[RowIn]) -> list[str]:
    """Отказ на каждую строку `old`, которой нет в `new`, — одни слова у `check` и `load`."""
    return [GONE.format(code) for code in missing(old, new)]


def diff(old: Sequence[RowIn], new: Sequence[RowIn]) -> list[tuple[str, str]]:
    """«Было — станет» из `old` в `new`: порядок общих строк, затем строки `new` по порядку —
    пары «код:поле» и строка вывода."""
    before = {row.code: row for row in old}
    found = _order(old, new)
    for row in new:
        was = before.get(row.code)
        found += _changes(was, row) if was else [(f"{row.code}:", f"новая строка {row.code}")]
    return found


def _order(old: Sequence[RowIn], new: Sequence[RowIn]) -> list[tuple[str, str]]:
    """Общие строки на других местах — по их порядку между собой, без кодов: новая строка в
    середине сдвигает номера мест ниже себя, но порядка не меняет."""
    common = {row.code for row in old} & {row.code for row in new}
    was, now = ([row.code for row in rows if row.code in common] for rows in (old, new))
    moved = sum(a != b for a, b in zip(was, now, strict=True))
    return [(":order", f"порядок: строк на других местах {moved}")] if moved else []


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
    """Пороги: новый, убранный, другое значение или единица — значением, а не записью; при
    смене единицы прежнее число — со своей: «3.25 → 3250 мс» читалось бы ростом в тысячу раз."""
    before, after = {p.name: p for p in was}, {p.name: p for p in now}
    found = []
    for name in sorted(before.keys() | after.keys()):
        old, new, key = before.get(name), after.get(name), f"{code}:param:{name}"
        if new is None:
            found.append((key, f"{code} · порог {name} убран"))
        elif old is None:
            found.append((key, f"{code} · {name} новый, {_value(new)}"))
        elif (old.value, old.unit) != (new.value, new.unit):
            start = _value(old) if old.unit != new.unit else _number(old.value)
            found.append((key, f"{code} · {name} {start} → {_value(new)}"))
    return found


def _value(param: ParamIn) -> str:
    """Число порога со словом единицы; у `number` слова нет."""
    return f"{_number(param.value)} {UNIT_WORDS[param.unit]}".rstrip()


class WriteFailedError(RuntimeError):
    """Сбой до фиксации: транзакция откатилась, справочник не тронут (A313)."""


class CommitFailedError(RuntimeError):
    """Сбой на фиксации: запись могла пройти — точный итог видит `status` (A313)."""


@dataclass(frozen=True)
class NewRevision:
    """Что записать: строки по порядку, источник и причина; у файла — его sha256 и дата снимка."""

    rows: Sequence[RowIn]
    source: RevisionSource
    reason: str
    file: ReferenceFile | None = None


@dataclass(frozen=True)
class Written:
    """Итог записи: номер активной редакции; у новой — строк и порогов; `already` — уже было."""

    number: int
    rows: int = 0
    params: int = 0
    already: bool = False


#: Решение под замком по активной редакции (или её отсутствию): что записать — или номер
#: активной, если записывать нечего («уже загружено»); отказ — `FileRefusedError`.
Decide = Callable[[AsyncSession, ReferenceRevision | None], Awaitable[NewRevision | int]]


async def write_revision(engine: AsyncEngine, decide: Decide) -> Written:
    """Запись редакции одной транзакцией: замок, активная после него, решение `decide`, запись и
    явная фиксация. Отказ уходит как есть, база не тронута; сбой до фиксации —
    `WriteFailedError`, на ней — `CommitFailedError`: тексты исключений в них не идут (L57)."""
    async with AsyncSession(engine, expire_on_commit=False) as session:
        try:
            await _lock(session)
            active = await _active(session)
            decided = await decide(session, active)
            if isinstance(decided, int):
                return Written(decided, already=True)
            written = await _insert(session, active, decided)
        except FileRefusedError:
            raise
        except Exception as failure:
            kind = type(failure).__name__
            raise WriteFailedError(f"сбой записи: {kind} — справочник не тронут") from failure
        try:
            await session.commit()
        except Exception as failure:
            kind = type(failure).__name__
            raise CommitFailedError(f"сбой на фиксации: {kind} — {MAYBE_WRITTEN}") from failure
    return written


async def _lock(session: AsyncSession) -> None:
    """Первые запросы транзакции: предел ожидания и замок `REFERENCE`; не дождались — отказ по
    коду 55P03, как `_timed_out` у `env.py` (decisions, проба 1)."""
    limit = LOCK_WAIT_SECONDS
    setting = text("SELECT set_config('lock_timeout', :value, true)")
    await session.execute(setting, {"value": f"{limit}s"})
    try:
        await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": locks.REFERENCE})
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) != _LOCK_NOT_AVAILABLE:
            raise
        raise FileRefusedError([BUSY.format(limit)]) from None


async def _active(session: AsyncSession) -> ReferenceRevision | None:
    try:
        return await active_revision(session)
    except NotLoadedError:
        return None


async def _insert(
    session: AsyncSession, active: ReferenceRevision | None, new: NewRevision
) -> Written:
    """Погасить прежнюю, вставить новую активную — номер на 1 больше наибольшего, — затем строки
    и пороги: обратный порядок упёрся бы в индекс одной активной."""
    if active is not None:
        archive = update(ReferenceRevision).where(ReferenceRevision.id == active.id)
        await session.execute(
            archive.values(status=RevisionStatus.ARCHIVED, archived_at=func.now())
        )
    highest = select(func.coalesce(func.max(ReferenceRevision.number), 0))
    number: int = (await session.execute(highest)).scalar_one() + 1
    source = new.file
    header = insert(ReferenceRevision).values(
        number=number,
        status=RevisionStatus.ACTIVE,
        source=new.source,
        based_on_id=active.id if active else None,
        file_sha256=source.sha256 if source else None,
        file_rows=len(new.rows) if source else None,
        source_date=source.source_date if source else None,
        reason=new.reason,
    )
    revision_id: int = (await session.execute(header.returning(ReferenceRevision.id))).scalar_one()
    params = 0
    for position, row in enumerate(new.rows, 1):
        params += await insert_row(session, revision_id, position, row)
    return Written(number, len(new.rows), params)


async def insert_row(session: AsyncSession, revision_id: int, position: int, row: RowIn) -> int:
    """Строка редакции на своём месте и её пороги с пределами единицы (Ч2.1); число порогов."""
    fields = {field: getattr(row, field) for field in _FIELDS}
    values = insert(ReferenceCheck).values(revision_id=revision_id, position=position, **fields)
    check_id = await session.scalar(values.returning(ReferenceCheck.id))
    for param in row.params:
        low, high = UNITS[param.unit]
        await session.execute(
            insert(ReferenceParam).values(
                check_id=check_id,
                name=param.name,
                value=param.value,
                min_value=low,
                max_value=high,
                unit=param.unit,
            )
        )
    return len(row.params)


async def load(
    engine: AsyncEngine,
    found: ReferenceFile,
    *,
    expect: tuple[str, int],
    reason: str,
    replace_edits: bool = False,
) -> Written:
    """Загрузка файла (A311, A314): под замком по порядку — содержимое равно активной («уже
    загружено» при любом знаке), знак, правка после файла без `--replace-edits`, пропавшая
    строка; затем запись. Равенство — пустое «было — станет» `diff`, порядок строк — в нём."""
    sha256, seen = expect

    async def decide(session: AsyncSession, active: ReferenceRevision | None) -> NewRevision | int:
        rows = await revision_rows(session, active.id) if active else []
        if active is not None and not diff(rows, found.rows):
            return active.number
        number = active.number if active else 0
        why = [FILE_CHANGED] if sha256 != found.sha256 else []
        why += [CHANGED.format(seen, number)] if seen != number else []
        edited = active is not None and active.source is RevisionSource.EDIT
        if not why and edited and not replace_edits:
            why = [EDITED.format(number)]
        why = why or missing_reasons(rows, found.rows)
        if why:
            raise FileRefusedError(why)
        return NewRevision(found.rows, RevisionSource.FILE, reason, found)

    return await write_revision(engine, decide)
