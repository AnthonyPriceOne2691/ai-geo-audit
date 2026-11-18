"""База моделей: соглашение об именах, карта типов, помощник перечисления.

Имена ограничений известны заранее: по имени из ответа базы (`constraint_name`) вызывающий
отличает «ключ занят» от «нарушена проверка», не разбирая текст ошибки. Карта типов даёт
`bigint`, `text` и `timestamptz` без явного типа: счётчик не переполнится, длинный адрес не
уронит пачку, время не потеряет пояс. Перечисление — строка с проверкой, типов ENUM в базе нет.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum, StrEnum

from sqlalchemy import BigInteger, DateTime, MetaData, Text, text
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
_CHECK = text(
    "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c "
    "JOIN pg_class t ON t.oid = c.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace "
    "WHERE c.conname = :name AND n.nspname = :schema AND t.relname = :table"
)


class NothingToCheck(RuntimeError):
    """Проверке нечего проверять: молчание выглядело бы как «нарушений нет»."""


class Base(DeclarativeBase):
    """База моделей сервиса: соглашение об именах и карта типов."""

    metadata = MetaData(naming_convention=NAMING)
    type_annotation_map = TYPES


def enum_column(enum_cls: type[Enum], *, name: str) -> SqlEnum:
    """Тип колонки-перечисления: значения, а не имена, строкой `ENUM_LENGTH` с проверкой
    `ck_<таблица>_<name>`. Только `StrEnum`: у него `str()` равен значению, и сравнение со
    строкой не может молча соврать.
    """
    if not (isinstance(enum_cls, type) and issubclass(enum_cls, StrEnum)):
        raise TypeError(
            f"помощник перечисления берёт только StrEnum: у {enum_cls!r} str() даёт имя, "
            "а не значение"
        )
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
        where = {"name": f"ck_{table.name}_{kind.name}", "schema": table.schema or "public"}
        definition = await conn.scalar(_CHECK, {**where, "table": table.name})
        in_db, in_code = set(re.findall(r"'([^']*)'", definition or "")), set(kind.enums)
        place = f"{table.fullname}.{column.name}"
        missing, extra = sorted(in_code - in_db), sorted(in_db - in_code)
        problems += [f"{place}: значения «{v}» нет в проверке базы" for v in missing]
        problems += [f"{place}: в проверке базы лишнее «{v}»" for v in extra]
    return problems
