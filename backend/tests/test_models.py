"""База моделей на настоящем Postgres: имена, типы, перечисления (A15, A16)."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum, StrEnum

import dbtools
import pytest
from dbtools import Probe, Status
from sqlalchemy import Column, MetaData, Table, Text, insert, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_geo_audit.storage import models, session_scope

NAMES = """
SELECT conname FROM pg_constraint WHERE conrelid = 'probe.probe'::regclass
UNION SELECT indexname FROM pg_indexes WHERE schemaname = 'probe' AND tablename = 'probe'
"""
TYPES = """
SELECT column_name, data_type FROM information_schema.columns
WHERE table_schema = 'probe' AND table_name = 'probe'
"""


async def test_a15_names_come_from_the_convention(engine: AsyncEngine) -> None:
    """A15: ограничения и индекс пробы названы по соглашению, умолчаний Postgres нет."""
    async with engine.connect() as conn:
        names = set((await conn.execute(text(NAMES))).scalars())
    expected = {"pk_probe", "uq_probe_key", "ix_probe_owner_id", "fk_probe_owner_id_owner"}
    assert expected | {"ck_probe_positive", "ck_probe_status"} == names


async def test_a15_unique_violation_is_named_by_the_database(committed: AsyncEngine) -> None:
    """A15: второй `key` из другого соединения — имя `uq_probe_key` из ответа базы."""
    async with session_scope(committed) as s:
        await s.execute(insert(dbtools.probe).values(key="taken"))
    with pytest.raises(IntegrityError) as caught:
        async with session_scope(committed) as s:
            await s.execute(insert(dbtools.probe).values(key="taken"))
    assert caught.value.orig.__cause__.constraint_name == "uq_probe_key"  # type: ignore[union-attr]


async def test_a15_default_types_are_bigint_text_timestamptz(committed: AsyncEngine) -> None:
    """A15: `Mapped[int]`, `[str]`, `[datetime]` без типа — bigint, text, timestamptz."""
    async with committed.connect() as conn:
        types = {name: kind for name, kind in await conn.execute(text(TYPES))}
    assert [types[c] for c in ("amount", "note", "seen_at")] == [
        "bigint",
        "text",
        "timestamp with time zone",
    ]
    big, wide, at = 3_000_000_000, "x" * 3000, datetime(2026, 11, 18, 12, tzinfo=UTC)
    async with session_scope(committed) as s:
        s.add(Probe("typed", amount=big, note=wide, seen_at=at))
    async with session_scope(committed) as s:
        row = (await s.execute(select(Probe).where(Probe.key == "typed"))).scalar_one()
        assert (row.amount, row.note, row.seen_at) == (big, wide, at)


async def test_a16_values_are_stored_and_the_check_rejects_the_rest(
    committed: AsyncEngine,
) -> None:
    """A16: в базе `done`, а не `DONE`; имя вместо значения отвергает `ck_probe_status`."""
    async with session_scope(committed) as s:
        s.add(Probe("enum", status=Status.DONE))
    async with committed.connect() as conn:
        stored = (await conn.execute(text("SELECT status FROM probe.probe"))).scalar_one()
        enums = (await conn.execute(text("SELECT count(*) FROM pg_type WHERE typtype = 'e'")))
        assert (stored, enums.scalar_one()) == ("done", 0)  # типов ENUM в базе нет вовсе
    with pytest.raises(IntegrityError) as caught:
        async with session_scope(committed) as s:
            await s.execute(text("INSERT INTO probe.probe (key, status) VALUES ('x', 'DONE')"))
    cause = caught.value.orig.__cause__
    assert cause.constraint_name == "ck_probe_status"  # type: ignore[union-attr]


def test_a16_column_length_is_set_by_the_helper_not_by_values() -> None:
    """A16: в перечисление добавлено значение длиннее прежних — длина колонки та же."""

    class Short(StrEnum):
        A = "a"

    class Longer(StrEnum):
        A = "a"
        LONG = "a-much-longer-value"

    lengths = {models.enum_column(e, name="kind").length for e in (Short, Longer)}
    assert lengths == {models.ENUM_LENGTH}


def test_a16_helper_takes_only_str_enum() -> None:
    """A16: обычный `Enum` — отказ словами; у `StrEnum` `str()` равен значению."""

    class Plain(Enum):
        DONE = "done"

    with pytest.raises(TypeError, match="только StrEnum"):
        models.enum_column(Plain, name="plain")
    assert (str(Status.DONE), Status.DONE == "done") == ("done", True)


async def test_a16_drift_is_empty_for_the_probe_and_names_an_added_value(
    engine: AsyncEngine,
) -> None:
    """A16: у пробы расхождений нет; значение, добавленное в Python без ревизии, — красная."""

    class StatusPlus(StrEnum):
        NEW = "new"
        DONE = "done"
        ARCHIVED = "archived"

    drifted = MetaData(schema=dbtools.PROBE_SCHEMA, naming_convention=models.NAMING)
    Table("probe", drifted, Column("status", models.enum_column(StatusPlus, name="status")))
    async with engine.connect() as conn:
        assert await models.enum_drift(conn, dbtools.ProbeBase.metadata) == []
        assert await models.enum_drift(conn, drifted) == [
            "probe.probe.status: значения «archived» нет в проверке базы"
        ]


async def test_a16_drift_refuses_when_no_enum_column_is_found(engine: AsyncEngine) -> None:
    """A16: колонок-перечислений сверка не нашла, хотя проба есть — отказ, а не «чисто»."""
    plain = MetaData(schema=dbtools.PROBE_SCHEMA)
    Table("probe", plain, Column("key", Text))
    async with engine.connect() as conn:
        with pytest.raises(models.NothingToCheck, match="просмотрено колонок 1"):
            await models.enum_drift(conn, plain)
