"""Таблицы наборов на настоящем Postgres (A320–A322, A338): ревизия `0004` и встроенный набор,
«база после ревизии» оснастки, ограничения по имени, триггер `check_set_frozen`, каталоги.

Строки до проверки ставят сырые вставки; у тестов без фиксаций — в транзакции с откатом."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import asyncpg
import dbtools
import pytest
from dbtools import public_tables, refused_by, version
from reference_sample import SAMPLE2
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_geo_audit import schema
from ai_geo_audit.sets import catalogs
from ai_geo_audit.sets.models import (
    CheckSet,
    CheckSetItem,
    CheckSetVersion,
    ItemKind,
    PlatformModels,
    SetKind,
)
from ai_geo_audit.storage import models

#: Таблицы наборов — по именам моделей: тест следит за моделями, а не за своим списком.
TABLES = {model.__tablename__ for model in (CheckSet, CheckSetVersion, CheckSetItem)}
#: Функция и триггеры неизменности: в метаданных моделей их нет, их несёт только ревизия.
FROZEN_OBJECTS = (
    "SELECT 'функция ' || proname FROM pg_proc WHERE proname = 'check_set_frozen' UNION ALL"
    " SELECT 'триггер ' || tgname FROM pg_trigger WHERE NOT tgisinternal"
    " AND tgname LIKE 'check_set%'"
)
FROZEN = ["триггер check_set_items_frozen", "триггер check_set_versions_frozen"]
FROZEN += ["триггер check_sets_frozen", "функция check_set_frozen"]
#: Наборы и версии: вид, имя, номер, статус, опубликована ли, строк состава.
STATE = (
    "SELECT s.kind, s.name, v.number, v.status, v.published_at IS NOT NULL,"
    " (SELECT count(*) FROM check_set_items i WHERE i.version_id = v.id)"
    " FROM check_sets s LEFT JOIN check_set_versions v ON v.set_id = s.id ORDER BY s.id, v.id"
)
ONE = [("all_checks", "Все проверки", 1, "active", True, 0)]
CLEAR = "TRUNCATE check_sets, check_set_versions, check_set_items RESTART IDENTITY"
KINDS = (ItemKind.CHECK, ItemKind.METRIC, ItemKind.ANSWER)


async def connect() -> asyncpg.Connection:
    return await asyncpg.connect(dbtools._raw(dbtools.own_url()))


async def fetch(query: str) -> list[tuple[Any, ...]]:
    conn = await connect()
    try:
        return [tuple(row) for row in await conn.fetch(query)]
    finally:
        await conn.close()


async def state() -> list[tuple[Any, ...]]:
    return await fetch(STATE)


async def frozen() -> list[str]:
    return sorted(row[0] for row in await fetch(FROZEN_OBJECTS))


async def raw(*statements: str) -> None:
    """Сырые команды по порядку в одной транзакции своего соединения — с фиксацией."""
    conn = await connect()
    try:
        async with conn.transaction():
            for statement in statements:
                await conn.execute(statement)
    finally:
        await conn.close()


async def test_a320_tables_trigger_and_builtin_set_come_and_go_with_0004() -> None:
    """A320: `alembic upgrade head` с пустой схемы — голова `0004`, три таблицы, функция и три
    триггера, набор «Все проверки»: вид `all_checks`, версия 1 `active`, состав пуст; `alembic
    check` — код 0; откат на `0003` убирает таблицы, функцию и триггеры; снова `upgrade head` —
    код 0, всё на месте и набор один."""
    await dbtools.empty_schema(dbtools.own_url())
    try:
        up = dbtools.alembic("upgrade", "head")
        head, made = await version(), await public_tables()
        objects, sets = await frozen(), await state()
        check = dbtools.alembic("check")
        down = dbtools.alembic("downgrade", "0003")
        left, left_objects = TABLES & await public_tables(), await frozen()
        again = dbtools.alembic("upgrade", "head")
        made_again, objects_again, sets_again = await public_tables(), await frozen(), await state()
    finally:
        await dbtools.prepare()
    runs = {"вверх": up, "check": check, "вниз": down, "снова вверх": again}
    assert {what: run.returncode for what, run in runs.items() if run.returncode} == {}
    assert (head, made >= TABLES, objects, sets) == ("0004", True, FROZEN, ONE)
    assert (left, left_objects) == (set(), [])
    assert (made_again >= TABLES, objects_again, sets_again) == (True, FROZEN, ONE)


async def test_a320_set_models_are_in_schema_and_follow_the_rules(engine: AsyncEngine) -> None:
    """A320: модели наборов — в `schema.py`; правила моделей (A15) и сверка перечислений с базой
    (A16) по `schema.py` — нарушений 0."""
    assert set(schema.metadata.tables) >= TABLES
    assert models.model_problems(schema.metadata) == []
    async with engine.connect() as conn:
        assert await models.enum_drift(conn, schema.metadata) == []


#: Остатки прошлого теста — сырыми командами по базе после ревизии — и таблица, что их назовёт.
LEFTOVERS = {
    "own-set": (["INSERT INTO check_sets (name, kind) VALUES ('Свой', 'custom')"], "check_sets"),
    "second-version": (
        [
            "INSERT INTO check_set_versions (set_id, number, status)"
            " SELECT id, 2, 'draft' FROM check_sets"
        ],
        "check_set_versions",
    ),
    "builtin-item": (
        [
            CLEAR,
            "INSERT INTO check_sets (name, kind) VALUES ('Все проверки', 'all_checks')",
            "INSERT INTO check_set_versions (set_id, number, status)"
            " SELECT id, 1, 'draft' FROM check_sets",
            "INSERT INTO check_set_items SELECT id, '91', 'check' FROM check_set_versions",
            "UPDATE check_set_versions SET status = 'active', published_at = now()",
        ],
        "check_set_items",
    ),
    "builtin-renamed": (
        [
            CLEAR,
            "INSERT INTO check_sets (name, kind) VALUES ('Другое', 'all_checks')",
            "INSERT INTO check_set_versions (set_id, number, status, published_at)"
            " SELECT id, 1, 'active', now() FROM check_sets",
        ],
        "check_sets",
    ),
}


@pytest.mark.parametrize(("statements", "table"), list(LEFTOVERS.values()), ids=list(LEFTOVERS))
async def test_a320_require_empty_names_a_leftover_of_the_sets(
    committed: AsyncEngine, statements: list[str], table: str
) -> None:
    """A320: база после ревизии — встроенный набор — не остаток: проверка оснастки проходит; лишний
    свой набор, вторая версия встроенного, строка состава встроенного, встроенный с другим именем —
    сырыми командами — «…прошлый тест не убрал за собой» с именем таблицы."""
    await dbtools.require_empty(committed)
    await raw(*statements)
    with pytest.raises(AssertionError) as left:
        await dbtools.require_empty(committed)
    assert str(left.value) == f"в public.{table} 1 строк(и): прошлый тест не убрал за собой"


async def test_a320_truncate_brings_back_one_builtin_set(committed: AsyncEngine) -> None:
    """A320: встроенный набор со строкой состава и лишний свой набор — `truncate` оснастки:
    встроенный набор снова один — вид, имя, версия 1 `active`, состав пуст; проверка проходит."""
    await raw(*LEFTOVERS["builtin-item"][0], *LEFTOVERS["own-set"][0])
    assert len(await state()) == 2
    await dbtools.truncate(committed)
    assert await state() == ONE
    await dbtools.require_empty(committed)


PUBLISHED = datetime(2026, 11, 27, 9, tzinfo=UTC)
#: Годные набор и активная версия: каждая проба меняет в них своё (L182).
SET: dict[str, Any] = {
    "id": 10,
    "name": "Входящий клиент",
    "kind": SetKind.CUSTOM,
    "description": "",
}
VERSION: dict[str, Any] = {
    "id": 10,
    "set_id": 10,
    "number": 1,
    "status": "active",
    "published_at": PUBLISHED,
}
DRAFT = {"status": "draft", "published_at": None}
ARCHIVED = {"status": "archived", "archived_at": PUBLISHED}
SET_CASES: list[tuple[str, list[dict[str, Any]], str | None]] = [
    ("name-empty", [{"name": ""}], "ck_check_sets_name"),
    ("name-201", [{"name": "и" * 201}], "ck_check_sets_name"),
    ("description-1001", [{"description": "о" * 1001}], "ck_check_sets_description"),
    ("name-again", [{}, {"id": 11}], "uq_check_sets_name"),
    # встроенный уже есть — база после ревизии
    ("second-all-checks", [{"kind": "all_checks"}], "uq_check_sets_all_checks"),
    ("kind-magic", [{"kind": "magic"}], "ck_check_sets_kind"),
    ("name-1-and-200", [{"name": "и"}, {"id": 11, "name": "и" * 200}], None),
    ("description-1000", [{"description": "о" * 1000}], None),
]
VERSION_CASES: list[tuple[str, list[dict[str, Any]], str | None]] = [
    ("second-active", [{}, {"id": 11, "number": 2}], "uq_check_set_versions_active"),
    ("second-draft", [DRAFT, DRAFT | {"id": 11, "number": 2}], "uq_check_set_versions_draft"),
    ("number-again", [ARCHIVED, {"id": 11}], "uq_check_set_versions_set_id_number"),
    ("number-0", [{"number": 0}], "ck_check_set_versions_number"),
    ("saves-minus-1", [{"saves": -1}], "ck_check_set_versions_saves"),
    ("status-lost", [{"status": "lost"}], "ck_check_set_versions_status"),
    ("models-economy", [{"platform_models": "economy"}], "ck_check_set_versions_platform_models"),
    ("draft-published", [{"status": "draft"}], "ck_check_set_versions_dates"),
    ("archived-without-time", [{"status": "archived"}], "ck_check_set_versions_dates"),
    ("active-unpublished", [{"published_at": None}], "ck_check_set_versions_dates"),
    ("active-archived", [{"archived_at": PUBLISHED}], "ck_check_set_versions_dates"),
    ("set-missing", [{"set_id": 99}], "fk_check_set_versions_set_id_check_sets"),
    ("number-1-saves-0-lite", [{"saves": 0, "platform_models": PlatformModels.LITE}], None),
    ("three-statuses", [ARCHIVED, {"id": 11, "number": 2}, DRAFT | {"id": 12, "number": 3}], None),
]


def sets(*changes: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [("check_sets", SET | change) for change in changes]


def versions(*changes: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    return [("check_sets", SET), *(("check_set_versions", VERSION | c) for c in changes)]


@pytest.mark.parametrize(
    ("inserts", "constraint"),
    [pytest.param(sets(*c), name, id=f"set-{case}") for case, c, name in SET_CASES]
    + [pytest.param(versions(*c), name, id=f"version-{case}") for case, c, name in VERSION_CASES],
)
async def test_a321_raw_set_and_version_insert_is_refused_by_its_own_constraint(
    inserts: list[tuple[str, dict[str, Any]]], constraint: str | None
) -> None:
    """A321: сырые вставки наборов и версий, каждая отдельно: имя пустое и из 201 знака, описание
    1001 знак, имя повтором, второй `all_checks`, вторая `active` и второй `draft` у набора, номер
    повтором, номер 0, `saves` −1, статус `lost`, `platform_models` — `economy`, черновик с
    `published_at`, `archived` без `archived_at`, активная без `published_at` и с `archived_at` —
    отказ базы по имени своего ограничения; годные соседи (L53) — тоже здесь."""
    assert await refused_by(*inserts) == constraint


ITEM: dict[str, Any] = {"version_id": 10, "code": "91", "kind": "check"}
CODE = "ck_check_set_items_code"
ITEM_CASES: list[tuple[str, dict[str, Any], str | None]] = [
    ("metric-91", {"kind": "metric"}, CODE),
    ("check-visibility_score", {"code": "visibility_score"}, CODE),
    ("metric-answer_mention", {"kind": "metric", "code": "answer_mention"}, CODE),
    ("answer-visibility_score", {"kind": "answer", "code": "visibility_score"}, CODE),
    ("answer-91", {"kind": "answer"}, CODE),
    ("check-answer_mention", {"code": "answer_mention"}, CODE),
    ("metric-41", {"kind": "metric", "code": "m" * 41}, CODE),
    ("answer-33", {"kind": "answer", "code": "answer_" + "a" * 33}, CODE),
    ("kind-extra", {"kind": "extra"}, "ck_check_set_items_kind"),
    ("version-missing", {"version_id": 99}, "fk_check_set_items_version_id_check_set_versions"),
    ("check-92.1", {"code": "92.1"}, None),
    ("metric-40", {"kind": "metric", "code": "m" * 40}, None),
    ("answer-32", {"kind": "answer", "code": "answer_" + "a" * 32}, None),
]


def items(*changes: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Набор, черновик и строки состава — с правками строк."""
    rows = [("check_set_items", ITEM | change) for change in changes]
    return [("check_sets", SET), ("check_set_versions", VERSION | DRAFT), *rows]


@pytest.mark.parametrize(
    ("inserts", "constraint"),
    [pytest.param(items(c), name, id=case) for case, c, name in ITEM_CASES]
    + [pytest.param(items({}, {}), "pk_check_set_items", id="code-twice")]
    + [pytest.param(items({}, {"code": "visibility_score", "kind": "metric"}), None, id="two")],
)
async def test_a321_raw_item_insert_is_refused_by_its_own_constraint(
    inserts: list[tuple[str, dict[str, Any]]], constraint: str | None
) -> None:
    """A321: сырые вставки строк состава черновика: вид `metric` с кодом `91`, `check` с
    `visibility_score`, `metric` с `answer_mention`, `answer` с `visibility_score`, `answer` с `91`,
    `check` с `answer_mention` — `ck_check_set_items_code`; вид `extra` — `ck_check_set_items_kind`;
    годные соседи и границы длины кода (L53) — тоже здесь."""
    assert await refused_by(*inserts) == constraint


async def test_a321_a_version_takes_base_models_and_zero_saves_by_default() -> None:
    """A321: набор без описания и версия без варианта моделей и знака правки — описание пустое,
    вариант `base`, `saves` 0."""
    conn = await connect()
    outer = conn.transaction()
    await outer.start()
    try:
        await conn.execute("INSERT INTO check_sets (id, name, kind) VALUES (10, 'Н', 'custom')")
        query = (
            "INSERT INTO check_set_versions (set_id, number, status) VALUES (10, 1, 'draft')"
            " RETURNING platform_models, saves, (SELECT description FROM check_sets WHERE id = 10)"
        )
        found = tuple(await conn.fetchrow(query))
    finally:
        await outer.rollback()
        await conn.close()
    assert found == ("base", 0, "")


#: Набор «Входящий клиент»: версия 1 архивная (`91`, `visibility_score`), 2 активная (`92`),
#: 3 — черновик (`93`); публикация и архив — теми командами, что пропускает триггер.
FROZEN_SETUP = (
    "INSERT INTO check_sets (id, name, kind) VALUES (10, 'Входящий клиент', 'custom')",
    "INSERT INTO check_set_versions (id, set_id, number, status) VALUES (10, 10, 1, 'draft')",
    "INSERT INTO check_set_items VALUES (10, '91', 'check'), (10, 'visibility_score', 'metric')",
    "UPDATE check_set_versions SET status = 'active', published_at = now() WHERE id = 10",
    "UPDATE check_set_versions SET status = 'archived', archived_at = now() WHERE id = 10",
    "INSERT INTO check_set_versions (id, set_id, number, status) VALUES (11, 10, 2, 'draft')",
    "INSERT INTO check_set_items VALUES (11, '92', 'check')",
    "UPDATE check_set_versions SET status = 'active', published_at = now() WHERE id = 11",
    "INSERT INTO check_set_versions (id, set_id, number, status) VALUES (12, 10, 3, 'draft')",
    "INSERT INTO check_set_items VALUES (12, '93', 'check')",
)
VERSION_FROZEN = (
    "check_set_frozen",
    "опубликованная версия набора не меняется — правка идёт черновиком",
)
SET_FROZEN = ("check_set_frozen", "вид и имя набора не меняются — нужное имя — копией")
VERSION_COMMANDS: dict[str, tuple[str, object]] = {
    "insert-into-active": (
        "INSERT INTO check_set_items VALUES (11, '94', 'check')",
        VERSION_FROZEN,
    ),
    "delete-from-active": ("DELETE FROM check_set_items WHERE version_id = 11", VERSION_FROZEN),
    "code-of-archived": (
        "UPDATE check_set_items SET code = '94' WHERE version_id = 10 AND code = '91'",
        VERSION_FROZEN,
    ),
    "models-of-active": (
        "UPDATE check_set_versions SET platform_models = 'lite' WHERE id = 11",
        VERSION_FROZEN,
    ),
    "delete-active": ("DELETE FROM check_set_versions WHERE id = 11", VERSION_FROZEN),
    "draft-row-to-active": (
        "UPDATE check_set_items SET version_id = 11 WHERE version_id = 12",
        VERSION_FROZEN,
    ),
    "active-row-to-draft": (
        "UPDATE check_set_items SET version_id = 12 WHERE version_id = 11",
        VERSION_FROZEN,
    ),
    "insert-into-draft": ("INSERT INTO check_set_items VALUES (12, '94', 'check')", "INSERT 0 1"),
    "code-of-draft": ("UPDATE check_set_items SET code = '94' WHERE version_id = 12", "UPDATE 1"),
    "delete-from-draft": ("DELETE FROM check_set_items WHERE version_id = 12", "DELETE 1"),
    "models-of-draft": (
        "UPDATE check_set_versions SET platform_models = 'lite' WHERE id = 12",
        "UPDATE 1",
    ),
    "archive-active": (
        "UPDATE check_set_versions SET status = 'archived', archived_at = now() WHERE id = 11",
        "UPDATE 1",
    ),
}
SET_COMMANDS: dict[str, tuple[str, object]] = {
    "custom-to-all-checks": ("UPDATE check_sets SET kind = 'all_checks' WHERE id = 10", SET_FROZEN),
    "builtin-to-custom": (
        "UPDATE check_sets SET kind = 'custom' WHERE kind = 'all_checks'",
        SET_FROZEN,
    ),
    "rename": ("UPDATE check_sets SET name = 'Другое' WHERE id = 10", SET_FROZEN),
    "describe": ("UPDATE check_sets SET description = 'новое' WHERE id = 10", "UPDATE 1"),
}


async def attempt(statement: str) -> tuple[object, bool]:
    """Набор `FROZEN_SETUP` в транзакции с откатом, затем `statement`: итог — строка статуса или
    (ограничение, текст) отказа — и те же ли после него строки наборов, версий и состава."""
    conn = await connect()
    outer = conn.transaction()
    await outer.start()
    try:
        for step in FROZEN_SETUP:
            await conn.execute(step)
        before = await rows_of(conn)
        try:
            async with conn.transaction():
                done: object = await conn.execute(statement)
        except asyncpg.RestrictViolationError as refused:
            done = (refused.constraint_name, str(refused))
        return done, await rows_of(conn) == before
    finally:
        await outer.rollback()
        await conn.close()


async def rows_of(conn: asyncpg.Connection) -> list[list[tuple[Any, ...]]]:
    tables = ("check_sets", "check_set_versions", "check_set_items")
    return [
        [tuple(row) for row in await conn.fetch(f"SELECT * FROM {table} ORDER BY 1, 2")]
        for table in tables
    ]


@pytest.mark.parametrize(
    ("statement", "expected"), list(VERSION_COMMANDS.values()), ids=list(VERSION_COMMANDS)
)
async def test_a322_published_versions_and_their_items_do_not_change(
    statement: str, expected: object
) -> None:
    """A322: сырыми командами — вставка и удаление строки состава у активной версии, правка кода у
    архивной, правка `platform_models` у активной, удаление активной, перенос строки состава
    черновика в активную и активной в черновик — отказ `check_set_frozen` «опубликованная версия
    набора не меняется — правка идёт черновиком», данные прежние; вставка, правка и удаление у
    черновика, правка черновика и архив активной — проходят."""
    assert await attempt(statement) == (expected, isinstance(expected, tuple))


@pytest.mark.parametrize(
    ("statement", "expected"), list(SET_COMMANDS.values()), ids=list(SET_COMMANDS)
)
async def test_a322_kind_and_name_of_a_set_do_not_change(statement: str, expected: object) -> None:
    """A322: сырыми командами у набора — вид `custom` → `all_checks`, вид встроенного `all_checks`
    → `custom`, новое имя — отказ `check_set_frozen` «вид и имя набора не меняются — нужное имя —
    копией», данные прежние; новое описание — проходит."""
    assert await attempt(statement) == (expected, isinstance(expected, tuple))


#: Каталоги — строками спеки (L37): код, название и у метрик признак «в GEO Score».
METRICS = [
    ("visibility_score", "AI Visibility Score", True),
    ("share_of_voice", "Share of Voice", True),
    ("brand_mentions", "Brand Mentions", False),
    ("prompt_coverage", "Prompt Coverage", True),
    ("citation_coverage", "Citation Coverage", True),
    ("source_coverage", "Source Coverage", False),
    ("competitor_visibility", "Competitor Visibility", False),
    ("source_gap", "Source Gap Analysis", True),
    ("prompt_gap", "Prompt Gap Analysis", True),
    ("historical_dynamics", "Historical Dynamics", True),
    ("platform_coverage", "AI Platform Comparison", False),
]
ANSWERS = [
    ("answer_mention", "Упоминание бренда"),
    ("answer_recommendation", "Рекомендация бренда"),
    ("answer_competitors", "Конкурентное преимущество"),
    ("answer_brand_facts", "Качество информации о бренде"),
    ("answer_as_source", "Использование как источник"),
    ("answer_recommendation_quality", "Качество рекомендаций"),
    ("answer_query_types", "Видимость по типам запросов"),
]


def test_a338_catalogs_are_the_rows_of_the_sheets() -> None:
    """A338: каталог метрик — 11 кодов, признак «в GEO Score» ровно у семи; каталог проверок
    выдачи — 7 кодов; оба — строками спеки в порядке листов."""
    assert [tuple(metric) for metric in catalogs.METRICS] == METRICS
    assert sum(metric.in_geo_score for metric in catalogs.METRICS) == 7
    assert [tuple(answer) for answer in catalogs.ANSWERS] == ANSWERS


async def item_refused(conn: asyncpg.Connection, code: str, kind: str) -> str | None:
    """Строка состава черновика 10 в точке сохранения с откатом: имя нарушенного ограничения."""
    point = conn.transaction()
    await point.start()
    try:
        await conn.execute("INSERT INTO check_set_items VALUES (10, $1, $2)", code, kind)
    except asyncpg.IntegrityConstraintViolationError as refused:
        return refused.constraint_name
    finally:
        await point.rollback()
    return None


async def test_a338_codes_of_the_three_kinds_do_not_overlap() -> None:
    """A338: строкой состава черновика каждый код каталогов и Ф2 (весь Ф и `95`) проходит только
    своим видом — метрика `metric`, проверка выдачи `answer`, код Ф `check`; чужим — отказ
    `ck_check_set_items_code`: ни один код каталогов не подходит под вид кода справочника, ни один
    код Ф — под вид метрики или проверки выдачи."""
    own = {code: ItemKind.METRIC for code, _, _ in METRICS}
    own |= {code: ItemKind.ANSWER for code, _ in ANSWERS}
    own |= {row["code"]: ItemKind.CHECK for row in SAMPLE2}
    conn = await connect()
    outer = conn.transaction()
    await outer.start()
    try:
        await conn.execute("INSERT INTO check_sets (id, name, kind) VALUES (10, 'Н', 'custom')")
        await conn.execute("INSERT INTO check_set_versions VALUES (10, 10, 1, 'draft')")
        found = {code: {k: await item_refused(conn, code, k) for k in KINDS} for code in own}
    finally:
        await outer.rollback()
        await conn.close()
    expected = {code: {k: None if k == kind else CODE for k in KINDS} for code, kind in own.items()}
    assert (len(own), found) == (25, expected)
