"""База моделей: соглашение об именах, карта типов, помощник перечисления.

Имена ограничений известны заранее: по имени из ответа базы (`constraint_name`) вызывающий
отличает «ключ занят» от «нарушена проверка», не разбирая текст ошибки. Карта типов даёт
`bigint`, `text` и `timestamptz` без явного типа: счётчик не переполнится, длинный адрес не
уронит пачку, время не потеряет пояс. Перечисление — строка с проверкой, типов ENUM в базе нет.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum, StrEnum

from sqlalchemy import BigInteger, DateTime, MetaData, Text
from sqlalchemy import Enum as SqlEnum
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
