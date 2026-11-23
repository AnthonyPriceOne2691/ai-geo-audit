"""База моделей: соглашение об именах, карта типов, помощник перечисления.

Имена ограничений известны заранее: по имени из ответа базы (`constraint_name`) вызывающий
отличает «ключ занят» от «нарушена проверка», не разбирая текст ошибки. Карта типов даёт
`bigint`, `text` и `timestamptz` без явного типа: счётчик не переполнится, длинный адрес не
уронит пачку, время не потеряет пояс. Явный узкий тип и время без пояса проверка правил
пропускает только поимённо. Перечисление — строка с проверкой, типов ENUM в базе нет.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from enum import Enum, StrEnum
from pathlib import Path

from sqlalchemy import BigInteger, DateTime, Float, Integer, MetaData, String, Text, text
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.orm import DeclarativeBase

#: Соглашение об именах ограничений и индексов (A15).
NAMING = {
    "pk": "pk_%(table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
}
#: Типы колонок `Mapped[...]` без явного типа (A15). Узкий тип — только поимённо, с причиной.
TYPES = {int: BigInteger(), str: Text(), datetime: DateTime(timezone=True)}
#: Длина строки колонки-перечисления: задаёт помощник, а не самое длинное значение (A16).
ENUM_LENGTH = 32
#: Сверка моделей с базой сравнивает и типы, и серверные умолчания — у `env.py` и у тестов.
COMPARE = {"compare_type": True, "compare_server_default": True}
#: Длиннее Postgres имя обрезает молча, и «имя известно заранее» перестаёт быть правдой.
PG_NAME_MAX = 63
#: Корень backend: от него ищется тест, названный для ключа с `SET NULL`.
BACKEND = Path(__file__).resolve().parents[3]
_CHECK = text(
    "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c "
    "JOIN pg_class t ON t.oid = c.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace "
    "WHERE c.conname = :name AND n.nspname = :schema AND t.relname = :table"
)


class NothingToCheck(RuntimeError):  # noqa: N818 -- имя в тестах и сообщениях
    """Проверке нечего проверять: молчание выглядело бы как «нарушений нет»."""


class Base(DeclarativeBase):
    """База моделей сервиса: соглашение об именах и карта типов."""

    metadata = MetaData(naming_convention=NAMING)
    type_annotation_map = TYPES


def enum_column(enum_cls: type[Enum], *, name: str) -> SqlEnum:
    """Тип колонки-перечисления: значения, а не имена, строкой `ENUM_LENGTH` с проверкой
    `ck_<таблица>_<name>`. Только `StrEnum`: у него `str()` равен значению, и сравнение со
    строкой не может молча соврать. Значение с апострофом — отказ: сверка значений читает
    проверку базы по кавычкам.
    """
    if not (isinstance(enum_cls, type) and issubclass(enum_cls, StrEnum)):
        raise TypeError(
            f"помощник перечисления берёт только StrEnum: у {enum_cls!r} str() даёт имя, "
            "а не значение"
        )
    if any("'" in member.value for member in enum_cls):
        raise ValueError(f"значение {enum_cls.__name__} с апострофом: сверка прочла бы его неверно")
    return SqlEnum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=ENUM_LENGTH,
        values_callable=lambda members: [member.value for member in members],
        validate_strings=True,
    )


async def enum_drift(conn: AsyncConnection, metadata: MetaData) -> list[str]:
    """Расхождения значений проверок `ck_…` в базе с перечислениями Python (A16).

    alembic проверки CHECK не сравнивает: значение, добавленное в Python без ревизии, всплыло бы
    на первой вставке в бою. Ни одной колонки-перечисления — отказ: «расхождений нет» на пустом
    месте было бы ложью.
    """
    found = [
        (table, column)
        for table in metadata.sorted_tables
        for column in table.columns
        if isinstance(column.type, SqlEnum) and not column.type.native_enum
    ]
    if not found:
        seen = sum(len(table.columns) for table in metadata.tables.values())
        raise NothingToCheck(f"колонок-перечислений не найдено: просмотрено колонок {seen}")
    problems = []
    for table, column in found:
        kind = column.type
        assert isinstance(kind, SqlEnum)
        where = {"name": f"ck_{table.name}_{kind.name}", "schema": table.schema or "public"}
        definition = await conn.scalar(_CHECK, {**where, "table": table.name})
        found_db = re.findall(r"'([^']*)'", definition or "")  # cpu-ok: колонки схемы
        in_db, in_code = set(found_db), set(kind.enums)
        place = f"{table.fullname}.{column.name}"
        missing, extra = sorted(in_code - in_db), sorted(in_db - in_code)
        problems += [f"{place}: значения «{v}» нет в проверке базы" for v in missing]
        problems += [f"{place}: в проверке базы лишнее «{v}»" for v in extra]
    return problems


def model_problems(
    metadata: MetaData,
    *,
    narrow: Mapping[str, str] | None = None,
    set_null: Mapping[str, str] | None = None,
    root: Path = BACKEND,
) -> list[str]:
    """Нарушения правил моделей (A15). Ни одной таблицы — отказ, а не «нарушений нет».

    - узкий тип (`Integer`, `String(n)`), плавающая точка и время без пояса (`DateTime()`) —
      только поимённо: `narrow` — «таблица.колонка» → причина. Колонки помощника перечисления
      не в счёт;
    - у внешнего ключа явный `ondelete`; `SET NULL` — только с тестом пути при обнулённой
      связи: `set_null` — имя ключа → «файл::тест», и тест должен быть на диске;
    - имя ограничения или индекса — не длиннее `PG_NAME_MAX` байт.
    """
    narrow, set_null = narrow or {}, set_null or {}
    tables = list(metadata.tables.values())
    if not tables:
        raise NothingToCheck("таблиц не найдено: просмотрено таблиц 0, колонок 0, ключей 0")
    problems = []
    for table in tables:
        for column in table.columns:
            kind, place = column.type, f"{table.name}.{column.name}"
            small = isinstance(kind, Integer) and not isinstance(kind, BigInteger)
            sized = isinstance(kind, String) and kind.length and not isinstance(kind, SqlEnum)
            naive = isinstance(kind, DateTime) and not kind.timezone
            loose = small or sized or naive or isinstance(kind, Float)
            if loose and not narrow.get(place, "").strip():
                problems.append(f"{place}: тип {kind} — только поимённо, с причиной")
        for fk in table.foreign_keys:
            assert fk.constraint is not None
            name = str(fk.constraint.name)
            if fk.ondelete is None:
                problems.append(f"{name}: нет явного ondelete")
            elif fk.ondelete.upper() == "SET NULL" and not _test_exists(root, set_null.get(name)):
                problems.append(f"{name}: SET NULL без теста пути при обнулённой связи")
        for named in [*(c.name for c in table.constraints), *(i.name for i in table.indexes)]:
            if len(str(named).encode()) > PG_NAME_MAX:
                problems.append(f"{named}: имя длиннее {PG_NAME_MAX} байт — Postgres обрежет")
    return sorted(problems)


def _test_exists(root: Path, ref: str | None) -> bool:
    """Тест «файл::имя» есть на диске: список без живого теста — та же дыра, что без списка."""
    path, _, name = (ref or "").partition("::")
    file = root / path
    if not (name and file.is_file()):
        return False
    pattern = rf"(?m)^(async )?def {re.escape(name)}\("
    return re.search(pattern, file.read_text(encoding="utf-8")) is not None
