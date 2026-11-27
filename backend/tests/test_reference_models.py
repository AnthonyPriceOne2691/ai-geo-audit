"""Таблицы справочника на настоящем Postgres (A300–A302): ревизия `0003`, ограничения по имени,
строки и пороги любой редакции под триггером `reference_frozen`."""

from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import asyncpg
import dbtools
import pytest
from dbtools import public_tables, refused_by, version
from reference_sample import SAMPLE, SAMPLE2, insert_revision

from ai_geo_audit.reference.models import ReferenceCheck, ReferenceParam, ReferenceRevision

#: Таблицы справочника — по именам моделей: тест следит за моделями, а не за своим списком.
TABLES = {model.__tablename__ for model in (ReferenceRevision, ReferenceCheck, ReferenceParam)}
#: Функция и триггеры неизменности: в метаданных моделей их нет, их несёт только ревизия.
FROZEN_OBJECTS = (
    "SELECT 'функция ' || proname FROM pg_proc WHERE proname = 'reference_frozen' UNION ALL"
    " SELECT 'триггер ' || tgname FROM pg_trigger WHERE NOT tgisinternal"
    " AND tgname LIKE 'reference%'"
)
FROZEN = ["триггер reference_checks_frozen", "триггер reference_params_frozen"]
FROZEN += ["функция reference_frozen"]
FROZEN_TEXT = "строки редакции справочника не меняются — правка идёт новой редакцией"


async def frozen_objects() -> list[str]:
    conn = await asyncpg.connect(dbtools._raw(dbtools.own_url()))
    try:
        return sorted(row[0] for row in await conn.fetch(FROZEN_OBJECTS))
    finally:
        await conn.close()


async def test_a300_tables_and_trigger_come_with_0003_and_go_with_its_downgrade() -> None:
    """A300, A320: `alembic upgrade head` с пустой схемы — голова цепочки (`dbtools.HEAD`: `0003`
    у Ч2.1, с Ч2.2 — `0004`), три таблицы, функция и два триггера неизменности; `alembic check` —
    модели против базы с флагами сверки — код 0; откат на ревизию до поставки (`0002`) убирает
    таблицы, функцию и триггеры; снова `upgrade head` — код 0 и всё на месте."""
    await dbtools.empty_schema(dbtools.own_url())
    try:
        up = dbtools.alembic("upgrade", "head")
        head, made, objects = await version(), await public_tables(), await frozen_objects()
        check = dbtools.alembic("check")
        down = dbtools.alembic("downgrade", "0002")
        left, left_objects = TABLES & await public_tables(), await frozen_objects()
        again = dbtools.alembic("upgrade", "head")
        made_again, objects_again = await public_tables(), await frozen_objects()
    finally:
        await dbtools.prepare()
    runs = {"вверх": up, "check": check, "вниз": down, "снова вверх": again}
    assert {what: run.returncode for what, run in runs.items() if run.returncode} == {}
    assert (head, made >= TABLES, objects) == (dbtools.HEAD, True, FROZEN)
    assert (left, left_objects) == (set(), [])
    assert (made_again >= TABLES, objects_again) == (True, FROZEN)


SHA = hashlib.sha256("Ф".encode()).hexdigest()
#: Годный заголовок редакции из файла: каждая проба меняет в нём своё.
REVISION: dict[str, Any] = {
    "id": 1,
    "number": 1,
    "status": "active",
    "source": "file",
    "file_sha256": SHA,
    "file_rows": 6,
    "source_date": date(2026, 11, 18),
    "reason": "проба",
}
EDIT = {"source": "edit", "file_sha256": None, "file_rows": None, "source_date": None, "reason": ""}
ARCHIVED = {"status": "archived", "archived_at": datetime(2026, 11, 26, tzinfo=UTC)}


def revisions(*changes: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Заголовки с id и номерами 1, 2…; у каждого — свои правки годного."""
    return [
        ("reference_revisions", REVISION | {"id": n, "number": n} | change)
        for n, change in enumerate(changes, 1)
    ]


REVISION_CASES = [
    ("second-active", [{}, {}], "uq_reference_revisions_active"),
    ("number-again", [ARCHIVED, {"number": 1}], "uq_reference_revisions_number"),
    ("number-0", [{"number": 0}], "ck_reference_revisions_number"),
    ("file-without-sha", [{"file_sha256": None}], "ck_reference_revisions_file"),
    ("sha-63", [{"file_sha256": SHA[:63]}], "ck_reference_revisions_file"),
    ("sha-65", [{"file_sha256": SHA + "0"}], "ck_reference_revisions_file"),
    ("sha-upper", [{"file_sha256": SHA.upper()}], "ck_reference_revisions_file"),
    ("edit-with-sha", [EDIT | {"file_sha256": SHA}], "ck_reference_revisions_file"),
    ("file-without-reason", [{"reason": ""}], "ck_reference_revisions_reason"),
    ("reason-501", [{"reason": "п" * 501}], "ck_reference_revisions_reason"),
    ("archived-without-time", [{"status": "archived"}], "ck_reference_revisions_archived_at"),
    ("file-without-rows", [{"file_rows": None}], "ck_reference_revisions_file"),
    ("file-without-date", [{"source_date": None}], "ck_reference_revisions_file"),
    ("edit-with-rows", [EDIT | {"file_rows": 6}], "ck_reference_revisions_file"),
    ("edit-with-date", [EDIT | {"source_date": date(2026, 11, 18)}], "ck_reference_revisions_file"),
    (
        "active-with-time",
        [{"archived_at": ARCHIVED["archived_at"]}],
        "ck_reference_revisions_archived_at",
    ),
    (
        "based-on-nothing",
        [{"based_on_id": 99}],
        "fk_reference_revisions_based_on_id_reference_revisions",
    ),
    ("file", [{}], None),
    ("edit", [EDIT], None),
    ("reason-500", [{"reason": "п" * 500}], None),
    ("archived-and-active", [ARCHIVED, {"based_on_id": 1}], None),
    ("two-archived", [ARCHIVED, ARCHIVED], None),
]


@pytest.mark.parametrize(
    ("changes", "constraint"),
    [pytest.param(changes, name, id=case) for case, changes, name in REVISION_CASES],
)
async def test_a301_raw_revision_insert_is_refused_by_its_own_constraint(
    changes: list[dict[str, Any]], constraint: str | None
) -> None:
    """A301: сырые вставки заголовков редакций, каждая отдельно: вторая `active`, номер повтором,
    номер 0, файл без sha256, sha256 из 63 знаков и заглавными, правка с sha256, файл без причины,
    причина 501 знак, `archived` без `archived_at` — отказ базы по имени своего ограничения; каждое
    условие проверки и годные соседи (L53) — тоже здесь."""
    assert await refused_by(*revisions(*changes)) == constraint


#: Годная строка справочника и её порог: каждая проба меняет своё.
CHECK: dict[str, Any] = {
    "id": 1,
    "revision_id": 1,
    "position": 1,
    "code": "92",
    "block": "блок 92",
    "name": "проверка 92",
    "method": "rules",
    "coverage": "full",
    "norm": "норма 92",
    "error": "ошибка 92",
    "recommendation": "рекомендация 92",
    "importance": "important",
    "scope": "pages",
    "share_percent": Decimal("10"),
}
PARAM: dict[str, Any] = {
    "check_id": 1,
    "name": "max_seconds",
    "value": Decimal("3.25"),
    "min_value": Decimal("0.1"),
    "max_value": Decimal("60"),
    "unit": "s",
}
SECOND_CHECK = CHECK | {"id": 2, "position": 2, "code": "93"}
CHECK_CASES: list[tuple[str, dict[str, Any], str | None]] = [
    ("code-9a", {"code": "9a"}, "ck_reference_checks_code"),
    ("code-123", {"code": "123"}, "ck_reference_checks_code"),
    ("code-9.123", {"code": "9.123"}, "ck_reference_checks_code"),
    ("name-201", {"name": "и" * 201}, "ck_reference_checks_names"),
    ("name-empty", {"name": ""}, "ck_reference_checks_names"),
    ("block-201", {"block": "б" * 201}, "ck_reference_checks_names"),
    ("block-empty", {"block": ""}, "ck_reference_checks_names"),
    ("norm-empty", {"norm": ""}, "ck_reference_checks_texts"),
    ("norm-4001", {"norm": "н" * 4001}, "ck_reference_checks_texts"),
    ("error-empty", {"error": ""}, "ck_reference_checks_texts"),
    ("error-4001", {"error": "о" * 4001}, "ck_reference_checks_texts"),
    ("recommendation-empty", {"recommendation": ""}, "ck_reference_checks_texts"),
    ("recommendation-4001", {"recommendation": "р" * 4001}, "ck_reference_checks_texts"),
    ("method-magic", {"method": "magic"}, "ck_reference_checks_method"),
    ("coverage-maybe", {"coverage": "maybe"}, "ck_reference_checks_coverage"),
    ("importance-urgent", {"importance": "urgent"}, "ck_reference_checks_importance"),
    ("scope-everywhere", {"scope": "everywhere"}, "ck_reference_checks_scope"),
    ("share-at-site", {"scope": "site"}, "ck_reference_checks_share_scope"),
    ("pages-without-share", {"share_percent": None}, "ck_reference_checks_share_scope"),
    (
        "sample-without-share",
        {"scope": "sample", "share_percent": None},
        "ck_reference_checks_share_scope",
    ),
    ("share-0", {"share_percent": Decimal("0")}, "ck_reference_checks_share_range"),
    ("share-100.5", {"share_percent": Decimal("100.5")}, "ck_reference_checks_share_range"),
    (
        "revision-missing",
        {"revision_id": 99},
        "fk_reference_checks_revision_id_reference_revisions",
    ),
    ("code-9", {"code": "9"}, None),
    ("code-92.1", {"code": "92.1"}, None),
    ("names-200", {"name": "и" * 200, "block": "б" * 200}, None),
    ("texts-4000", {"norm": "н" * 4000, "error": "о" * 4000, "recommendation": "р" * 4000}, None),
    ("site-without-share", {"scope": "site", "share_percent": None}, None),
    ("share-100", {"share_percent": Decimal("100")}, None),
    ("share-0.01", {"share_percent": Decimal("0.01")}, None),
]
PARAM_CASES: list[tuple[str, dict[str, Any], str | None]] = [
    ("name-Max", {"name": "Max"}, "ck_reference_params_name"),
    ("name-digit-first", {"name": "1x"}, "ck_reference_params_name"),
    ("name-41", {"name": "a" * 41}, "ck_reference_params_name"),
    ("value-61", {"value": Decimal("61")}, "ck_reference_params_in_range"),
    ("value-0.05", {"value": Decimal("0.05")}, "ck_reference_params_in_range"),
    (
        "min-is-max",
        {"min_value": Decimal("3.25"), "max_value": Decimal("3.25")},
        "ck_reference_params_bounds",
    ),
    ("unit-furlong", {"unit": "furlong"}, "ck_reference_params_unit"),
    ("check-missing", {"check_id": 99}, "fk_reference_params_check_id_reference_checks"),
    ("name-40", {"name": "a" * 40}, None),
    ("name-with-digit", {"name": "p95_ms"}, None),
    ("value-60", {"value": Decimal("60")}, None),
    ("value-0.1", {"value": Decimal("0.1")}, None),
]
TWICE = [
    (
        "code-twice",
        ("reference_checks", SECOND_CHECK | {"code": "92"}),
        "uq_reference_checks_revision_id_code",
    ),
    (
        "position-twice",
        ("reference_checks", SECOND_CHECK | {"position": 1}),
        "uq_reference_checks_revision_id_position",
    ),
    ("param-twice", ("reference_params", PARAM), "uq_reference_params_check_id_name"),
    ("another-row-and-param", ("reference_checks", SECOND_CHECK), None),
]


def one_row(check: dict[str, Any], param: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Годная редакция с одной строкой и одним порогом — с правками строки и порога."""
    return [
        ("reference_revisions", REVISION),
        ("reference_checks", CHECK | check),
        ("reference_params", PARAM | param),
    ]


@pytest.mark.parametrize(
    ("inserts", "constraint"),
    [pytest.param(one_row(c, {}), name, id=f"row-{case}") for case, c, name in CHECK_CASES]
    + [pytest.param(one_row({}, p), name, id=f"param-{case}") for case, p, name in PARAM_CASES]
    + [pytest.param([*one_row({}, {}), more], name, id=case) for case, more, name in TWICE],
)
async def test_a301_raw_row_and_param_insert_is_refused_by_its_own_constraint(
    inserts: list[tuple[str, dict[str, Any]]], constraint: str | None
) -> None:
    """A301: сырые вставки строк и порогов, каждая отдельно: код `92` дважды в редакции, место
    повтором, код `9a`, имя 201 знак, пустая норма, норма 4001 знак, способ `magic`, охват `maybe`,
    важность `urgent`, область `everywhere`, доля у строки «сайт», «все страницы» без доли, доля 0 и
    100.5, порог `max_seconds` дважды у строки, имя порога `Max`, порог 61 при пределах 0.1–60,
    `min` = `max`, единица `furlong` — отказ базы по имени своего ограничения; каждое условие
    проверки и годные соседи (L53) — тоже здесь."""
    assert await refused_by(*inserts) == constraint


#: Правки строк и порогов сырыми командами; `$1` — id редакции.
CHANGES = {
    "update-param-92": (
        "UPDATE reference_params SET value = 3.5 WHERE check_id IN"
        " (SELECT id FROM reference_checks WHERE revision_id = $1 AND code = '92')"
    ),
    "update-norm-93": (
        "UPDATE reference_checks SET norm = 'другая' WHERE revision_id = $1 AND code = '93'"
    ),
    "delete-param-92": (
        "DELETE FROM reference_params WHERE check_id IN"
        " (SELECT id FROM reference_checks WHERE revision_id = $1 AND code = '92')"
    ),
    "delete-row-94": "DELETE FROM reference_checks WHERE revision_id = $1 AND code = '94'",
}
SNAPSHOT = (
    "SELECT c.revision_id, c.position, c.code, c.block, c.name, c.method, c.coverage, c.norm,"
    " c.error, c.recommendation, c.importance, c.scope, c.share_percent, p.name, p.value,"
    " p.min_value, p.max_value, p.unit FROM reference_checks c"
    " LEFT JOIN reference_params p ON p.check_id = c.id ORDER BY c.id, p.id"
)


@pytest.mark.parametrize("statement", list(CHANGES.values()), ids=list(CHANGES))
async def test_a302_rows_and_params_of_every_revision_do_not_change(statement: str) -> None:
    """A302: редакции 1 из Ф (`archived`) и 2 из Ф2 (`active`) — сырыми вставками помощника; в
    каждой сырой командой `UPDATE` порога `92`, `UPDATE` нормы `93`, `DELETE` порога `92`, `DELETE`
    строки `94` — отказ базы `reference_frozen` со своими словами; строки и пороги обеих редакций на
    месте, значения прежние."""
    conn = await asyncpg.connect(dbtools._raw(dbtools.own_url()))
    outer = conn.transaction()
    await outer.start()
    refusals = []
    try:
        first = await insert_revision(conn, SAMPLE, number=1, status="archived")
        second = await insert_revision(conn, SAMPLE2, number=2, based_on=first)
        before = [tuple(row) for row in await conn.fetch(SNAPSHOT)]
        for revision in (first, second):
            try:
                async with conn.transaction():
                    await conn.execute(statement, revision)
            except asyncpg.RestrictViolationError as refused:
                refusals.append((refused.constraint_name, str(refused)))
        after = [tuple(row) for row in await conn.fetch(SNAPSHOT)]
    finally:
        await outer.rollback()
        await conn.close()
    assert refusals == [("reference_frozen", FROZEN_TEXT)] * 2
    assert (len(before), after) == (13, before)
