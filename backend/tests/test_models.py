"""База моделей на настоящем Postgres: имена, типы, перечисления (A15, A16)."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum, StrEnum

import dbtools
import pytest
from dbtools import Probe, Status
from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    insert,
    select,
    text,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_geo_audit.storage import models, session_scope

NAMES = """
SELECT conname FROM pg_constraint WHERE conrelid = 'probe.probe'::regclass
UNION SELECT indexname FROM pg_indexes WHERE schemaname = 'probe' AND tablename = 'probe'
"""
HERE = "tests/test_models.py::"
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


def test_a16_helper_refuses_an_apostrophe_in_a_value() -> None:
    """A16: значение с апострофом — отказ словами: в проверке базы оно стоит как `'it''s'`, и
    сверка прочла бы два значения вместо одного."""

    class Quoted(StrEnum):
        IT = "it's"

    with pytest.raises(ValueError, match="апостроф"):
        models.enum_column(Quoted, name="quoted")


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


async def test_a16_drift_names_a_value_left_in_the_database(engine: AsyncEngine) -> None:
    """A16: значение убрали из Python, ревизии нет — в проверке базы оно осталось: красная."""

    class StatusLess(StrEnum):
        NEW = "new"

    left = MetaData(schema=dbtools.PROBE_SCHEMA, naming_convention=models.NAMING)
    Table("probe", left, Column("status", models.enum_column(StatusLess, name="status")))
    async with engine.connect() as conn:
        assert await models.enum_drift(conn, left) == [
            "probe.probe.status: в проверке базы лишнее «done»"
        ]


async def test_a16_drift_refuses_when_no_enum_column_is_found(engine: AsyncEngine) -> None:
    """A16: колонок-перечислений сверка не нашла, хотя проба есть — отказ, а не «чисто»."""
    plain = MetaData(schema=dbtools.PROBE_SCHEMA)
    Table("probe", plain, Column("key", Text))
    async with engine.connect() as conn:
        with pytest.raises(models.NothingToCheck, match="просмотрено колонок 1"):
            await models.enum_drift(conn, plain)


def test_a15_rules_pass_the_probe_and_catch_narrow_and_float_types() -> None:
    """A15: честная проба проходит; узкий тип и плавающая точка без причины — красная."""
    assert models.model_problems(dbtools.ProbeBase.metadata) == []
    m = MetaData(naming_convention=models.NAMING)
    Table(
        "t",
        m,
        Column("id", BigInteger, primary_key=True),
        Column("n", Integer),
        Column("code", String(8)),
        Column("ratio", Float),
        Column("iso", String(2)),
        Column("kind", models.enum_column(Status, name="kind")),
    )
    problems = models.model_problems(m, narrow={"t.iso": "код страны ISO — ровно 2 знака"})
    assert [p.split(":")[0] for p in problems] == ["t.code", "t.n", "t.ratio"]
    no_reason = models.model_problems(m, narrow={"t.iso": " "})
    assert "t.iso" in [p.split(":")[0] for p in no_reason]  # пустая причина — не причина


def test_a15_rules_catch_time_without_zone() -> None:
    """A15: явный `DateTime()` без пояса карта типов не спасает — без причины красная; время с
    поясом проходит."""
    m = MetaData(naming_convention=models.NAMING)
    at, stamp = Column("at", DateTime()), Column("stamp", DateTime(timezone=True))
    Table("t", m, Column("id", BigInteger, primary_key=True), at, stamp)
    assert models.model_problems(m) == ["t.at: тип DATETIME — только поимённо, с причиной"]
    assert models.model_problems(m, narrow={"t.at": "внешняя система отдаёт время без пояса"}) == []


def test_a15_rules_catch_keys_without_ondelete_and_untested_set_null() -> None:
    """A15: ключ без `ondelete` и `SET NULL` без живого теста — красная; с тестом — проходит."""
    m = MetaData(naming_convention=models.NAMING)
    Table("owner", m, Column("id", BigInteger, primary_key=True))
    Table(
        "child",
        m,
        Column("id", BigInteger, primary_key=True),
        Column("a_id", BigInteger, ForeignKey("owner.id")),
        Column("b_id", BigInteger, ForeignKey("owner.id", ondelete="SET NULL")),
        Column("c_id", BigInteger, ForeignKey("owner.id", ondelete="SET NULL")),
    )
    set_null = {
        "fk_child_b_id_owner": HERE + "test_missing_on_purpose",
        "fk_child_c_id_owner": HERE
        + "test_a15_rules_catch_keys_without_ondelete_and_untested_set_null",
    }
    assert models.model_problems(m, set_null=set_null) == [
        "fk_child_a_id_owner: нет явного ondelete",
        "fk_child_b_id_owner: SET NULL без теста пути при обнулённой связи",
    ]


def test_a15_rules_catch_names_longer_than_postgres_keeps() -> None:
    """A15: имя длиннее 63 знаков — красная с именем: Postgres обрезал бы его молча."""
    m = MetaData(naming_convention=models.NAMING)
    long = "t" * 60
    Table(long, m, Column("id", BigInteger, primary_key=True), Column("code", Text, unique=True))
    assert models.model_problems(m) == [f"uq_{long}_code: имя длиннее 63 байт — Postgres обрежет"]


def test_a15_rules_catch_a_long_index_name() -> None:
    """A15: длину имени проверка меряет и у индекса, а не только у ограничений."""
    m = MetaData(naming_convention=models.NAMING)
    name = "ix_" + "i" * 61
    columns = (Column("id", BigInteger, primary_key=True), Column("code", Text))
    Table("t", m, *columns, Index(name, "code"))
    assert models.model_problems(m) == [f"{name}: имя длиннее 63 байт — Postgres обрежет"]


def test_a15_rules_measure_names_in_bytes() -> None:
    """A15: Postgres режет имя по байтам: `pk_` и 31 буква кириллицы — 34 знака, но 65 байт."""
    m = MetaData(naming_convention=models.NAMING)
    table = "т" * 31
    Table(table, m, Column("id", BigInteger, primary_key=True))
    assert models.model_problems(m) == [f"pk_{table}: имя длиннее 63 байт — Postgres обрежет"]


def test_a15_rules_refuse_empty_metadata() -> None:
    """A15: таблиц нет — отказ с числом просмотренного, а не «нарушений нет»."""
    with pytest.raises(models.NothingToCheck, match="таблиц 0, колонок 0, ключей 0"):
        models.model_problems(MetaData())


def test_a15_service_models_follow_the_rules() -> None:
    """A15: модели сервиса. Пока таблиц нет — ждём отказа; первая таблица Части 3 сама переводит
    тест на настоящую проверку. Печатает, какой ветвью прошёл."""
    tables = models.Base.metadata.tables
    if not tables:
        print("ветвь: у сервиса таблиц нет — ждём отказа")
        with pytest.raises(models.NothingToCheck):
            models.model_problems(models.Base.metadata)
        return
    keys = sum(len(t.foreign_keys) for t in tables.values())
    print(f"ветвь: таблиц {len(tables)}, колонок {sum(len(t.columns) for t in tables.values())}, "
          f"ключей {keys}")
    assert models.model_problems(models.Base.metadata) == []


async def test_a16_service_enum_columns_match_the_database(engine: AsyncEngine) -> None:
    """A16: сверка перечислений на моделях сервиса. Колонок-перечислений нет — ждём отказа;
    первая такая колонка Части 3 сама переводит тест на сверку с базой после `upgrade head`.
    Печатает, какой ветвью прошёл."""
    tables = models.Base.metadata.tables.values()
    enums = [c for t in tables for c in t.columns if isinstance(c.type, SqlEnum)]
    async with engine.connect() as conn:
        if not enums:
            print("ветвь: колонок-перечислений у сервиса нет — ждём отказа")
            with pytest.raises(models.NothingToCheck):
                await models.enum_drift(conn, models.Base.metadata)
            return
        print(f"ветвь: колонок-перечислений {len(enums)}")
        assert await models.enum_drift(conn, models.Base.metadata) == []
