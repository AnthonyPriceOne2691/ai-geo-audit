"""Правка порогов, доли и важности (A315–A319): `change_thresholds` в своей сессии на настоящих
фиксациях; гонки — на своих соединениях, замок держит соединение теста, ожидание видно в
`pg_locks`. Редакции до правки ставит помощник сырых вставок; после теста таблицы пусты."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

import asyncpg
import dbtools
import pytest
from reference_sample import SAMPLE, insert_all
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from test_reference_check import F2
from test_reference_load import CHANGED, HEADS, holding, numbers

from ai_geo_audit.reference import file, store
from ai_geo_audit.reference.models import Unit
from ai_geo_audit.storage import locks

D = Decimal
ONE = [{"rows": SAMPLE, "number": 1}]
CHANGES = [("92", "param:max_seconds", D("3.5")), ("91", "importance", "important")]
REPORT = ["92 · max_seconds 3.25 → 3.5 с", "91 · важность: Критично → Важно", "редакция 1 → 2"]
STALE = "справочник уже изменён: вы смотрели {}, активна {} — перечитайте"
NOT_EXACT = "92 · max_seconds — нужно точное число"
SHARE = "92 · доля — больше 0 и не больше 100"
FINE = "92 · {} — не больше 6 знаков после точки и 15 значащих"
TEXTS = "тексты строки этой функцией не правятся"
SNAPSHOT = (
    "SELECT r.id, r.number, r.source, r.based_on_id, r.file_sha256, r.file_rows, r.source_date,"
    " r.reason, r.created_at, row_to_json(c)::text, row_to_json(p)::text"
    " FROM reference_revisions r JOIN reference_checks c ON c.revision_id = r.id"
    " LEFT JOIN reference_params p ON p.check_id = c.id WHERE r.number = $1"
    " ORDER BY c.position, p.id"
)


async def edit(engine: AsyncEngine, *changes: Any, expected: int = 1, apply: bool = True) -> Any:
    """`change_thresholds` в своей сессии; модуль — здесь: без кода среза тест красный сам. Запись
    завершает свою транзакцию — фиксацией, отказом или сбоем: замок снят и при открытой сессии
    (A313, A319)."""
    from ai_geo_audit.reference import edit as module

    async with AsyncSession(engine, expire_on_commit=False) as session:
        try:
            return await module.change_thresholds(
                session, expected=expected, changes=list(changes), apply=apply
            )
        finally:
            assert not (apply and session.in_transaction())


async def refused(engine: AsyncEngine, *changes: Any, **kw: Any) -> list[str]:
    """Причины отказа правки; правка без отказа — ошибка теста."""
    with pytest.raises(file.FileRefusedError) as refusal:
        await edit(engine, *changes, **kw)
    return refusal.value.reasons


async def fetch(query: str, *args: Any) -> list[tuple[Any, ...]]:
    conn = await asyncpg.connect(dbtools._raw(dbtools.own_url()))
    try:
        return [tuple(row) for row in await conn.fetch(query, *args)]
    finally:
        await conn.close()


async def rows_of(engine: AsyncEngine, number: int) -> list[Any]:
    """Строки редакции `number` тем видом, что сравнивает `diff`."""
    query = "SELECT id FROM reference_revisions WHERE number = $1"
    async with AsyncSession(engine) as session:
        return await store.revision_rows(session, (await fetch(query, number))[0][0])


async def test_a315_a_preview_reports_and_writes_nothing(committed: AsyncEngine) -> None:
    """A315: активна 1 из Ф; правка `92` и `91` с `apply=False` — отчёт строками `diff` в порядке
    правок и «редакция 1 → 2»; в базе по-прежнему одна редакция."""
    await insert_all(ONE)
    assert await edit(committed, *CHANGES, apply=False) == REPORT
    assert await numbers() == [(1, "active")]


async def test_a316_an_edit_is_revision_2_on_revision_1(committed: AsyncEngine) -> None:
    """A316: то же с `apply=True` — редакция 2 активна, источник «правка», основа 1, причина
    пустая; 1 в архиве; `diff(1, 2)` — ровно эти две правки; отчёт тот же, что у предпросмотра."""
    await insert_all(ONE)
    assert await edit(committed, *CHANGES) == REPORT
    assert [h[:4] + h[6:7] for h in await fetch(HEADS)] == [
        (1, "archived", "file", None, "проба 1"),
        (2, "active", "edit", 1, ""),
    ]
    assert store.diff(await rows_of(committed, 1), await rows_of(committed, 2)) == [
        ("91:importance", "91 · важность: Критично → Важно"),
        ("92:param:max_seconds", "92 · max_seconds 3.25 → 3.5 с"),
    ]


async def test_a317_earlier_revisions_do_not_change_after_later_ones(
    committed: AsyncEngine,
) -> None:
    """A317: снимок редакции 1 — все колонки строк и порогов и заголовок без статуса и
    `archived_at`; затем 2 правкой и 3 загрузкой Ф2 с `--replace-edits`: снимок 1 после 2 и 3
    равен снимку до них, снимок 2 после 3 — тоже; 1 и 2 в архиве, 3 активна."""
    await insert_all(ONE)
    first = await fetch(SNAPSHOT, 1)
    await edit(committed, ("92", "param:max_seconds", D("3.5")))
    second = await fetch(SNAPSHOT, 2)
    found = file.parse(F2)
    await store.load(committed, found, expect=(found.sha256, 2), reason="п", replace_edits=True)
    assert (await fetch(SNAPSHOT, 1), await fetch(SNAPSHOT, 2)) == (first, second)
    assert await numbers() == [(1, "archived"), (2, "archived"), (3, "active")]


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        (("77", "param:max_seconds", D("3.5")), "кода 77 нет в редакции 1"),
        (("92", "param:min_ms", D("5")), "92: порога min_ms у строки нет"),
        (("92", "param:max_seconds", D("61")), "92 · max_seconds: 61 вне пределов 0.1–60 с"),
        (
            ("92", "param:max_seconds", D("Infinity")),
            "92 · max_seconds: Infinity вне пределов 0.1–60 с",
        ),
        (("92", "param:max_seconds", True), NOT_EXACT),
        (("92", "param:max_seconds", 3.5), NOT_EXACT),
        (("92", "param:max_seconds", D("NaN")), NOT_EXACT),
        (("92", "param:max_seconds", D("sNaN")), NOT_EXACT),
        (
            ("92", "param:max_seconds", D("3.25000000000000000000000000001")),
            FINE.format("max_seconds"),
        ),
        (("92", "param:max_seconds", D("3.1234567")), FINE.format("max_seconds")),
        (("92", "share_percent", D("1E-16384")), FINE.format("доля")),
        (("92", "share_percent", D("1234567890.123456")), FINE.format("доля")),
        (("92", "share_percent", D("123456789.123456")), SHARE),
        (("92", "share_percent", D("0.00000000")), SHARE),
        (("92", "share_percent", D("0")), SHARE),
        (("92", "share_percent", D("100.5")), SHARE),
        (("92", "share_percent", 20), "92 · доля — нужно точное число"),
        (("92", "share_percent", D("10.0")), "92 · доля: значение то же — ничего не меняется"),
        (("91", "share_percent", D("20")), "91 · у проверки по сайту доли нет"),
        (("91", "importance", "urgent"), "91 · важность — Критично, Важно или Низкий приоритет"),
        (("91", "importance", "critical"), "91 · важность: значение то же — ничего не меняется"),
        (
            ("92", "param:max_seconds", D("3.250")),
            "92 · max_seconds: значение то же — ничего не меняется",
        ),
        (
            ("92", "param:max_seconds", D("3.2500000000")),
            "92 · max_seconds: значение то же — ничего не меняется",
        ),
        (("92", "block", "другой блок"), TEXTS),
        (("92", "name", "другое имя"), TEXTS),
        (("92", "norm", "другая норма"), TEXTS),
        (("92", "error", "другая ошибка"), TEXTS),
        (("92", "recommendation", "другая рекомендация"), TEXTS),
        (("92", "scope", "site"), "поле scope этой функцией не правится"),
    ],
    ids=[
        "no-code",
        "no-param",
        "above-limit",
        "infinity",
        "bool",
        "float",
        "nan",
        "snan",
        "30-significant",
        "7-places",
        "share-1e-16384",
        "share-16-significant",
        "share-15-significant-then-limit",
        "share-0-with-8-zeros",
        "share-0",
        "share-100.5",
        "share-int",
        "share-same",
        "share-at-site",
        "importance-word",
        "importance-same",
        "same-3.250",
        "same-with-trailing-zeros",
        "text-block",
        "text-name",
        "text-norm",
        "text-error",
        "text-recommendation",
        "other-field",
    ],
)
async def test_a318_a_bad_change_is_refused_with_its_reason(
    committed: AsyncEngine, change: tuple[str, str, object], reason: str
) -> None:
    """A318: каждая негодная правка отдельно — своя причина словами, с кодом и полем, число
    предела — из редакции; NaN и sNaN — «нужно точное число» до сравнения с пределом, а не
    исключение; тоньше 6 знаков после точки или 15 значащих — отказ до предела, 15 значащих —
    дальше к пределу (сверка среза 2: отчёт округлял, запись падала); хвостовые нули знаками не
    считаются — ни у числа, ни у нуля; все пять текстовых полей — «тексты…»; редакция
    по-прежнему одна."""
    await insert_all(ONE)
    assert (await refused(committed, change), await numbers()) == ([reason], [(1, "active")])


async def test_a318_a_repeat_nothing_and_two_bad_changes_are_refused(
    committed: AsyncEngine,
) -> None:
    """A318: `param:max_seconds` дважды — «правка повторяется»; `changes=[]` — «правок нет» при
    `apply=False` и `apply=True`; две негодные правки в одном вызове — две причины в порядке
    правок; редакция по-прежнему одна."""
    await insert_all(ONE)
    twice = [("92", "param:max_seconds", D("3.5")), ("92", "param:max_seconds", D("4"))]
    assert await refused(committed, *twice) == ["92 · max_seconds: правка повторяется"]
    nothing = ["правок нет — ничего не меняется"]
    assert [await refused(committed, apply=a) for a in (False, True)] == [nothing, nothing]
    two = [("77", "param:max_seconds", D("3.5")), ("92", "share_percent", D("0"))]
    assert await refused(committed, *two) == ["кода 77 нет в редакции 1", SHARE]
    assert await numbers() == [(1, "active")]


async def test_a318_the_limits_and_the_neighbours_are_those_of_the_revision(
    committed: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A318: пределы `92` в редакции 1 — 1–5 с (вставка с подменой таблицы единиц): 6 и 0.9 —
    «вне пределов 1–5 с», хотя таблица пускает 0.1–60; нижний предел 1 и доля с 6 знаками после
    точки — годны (предпросмотр); 5 и доля 100 — годны, запись; у порога редакции 2 те же пределы
    1–5."""
    with monkeypatch.context() as patch:
        patch.setitem(file.UNITS, Unit.SECONDS, (D("1"), D("5")))
        await insert_all(ONE)
    outside = [await refused(committed, ("92", "param:max_seconds", D(v))) for v in ("6", "0.9")]
    assert outside == [[f"92 · max_seconds: {v} вне пределов 1–5 с"] for v in ("6", "0.9")]
    low = [("92", "param:max_seconds", D("1")), ("92", "share_percent", D("99.123456"))]
    preview = ["92 · max_seconds 3.25 → 1 с", "92 · доля: 10 % → 99.123456 %", "редакция 1 → 2"]
    assert await edit(committed, *low, apply=False) == preview
    edge = [("92", "param:max_seconds", D("5")), ("92", "share_percent", D("100"))]
    lines = ["92 · max_seconds 3.25 → 5 с", "92 · доля: 10 % → 100 %", "редакция 1 → 2"]
    assert await edit(committed, *edge) == lines
    limits = "SELECT p.min_value, p.max_value FROM reference_params p JOIN reference_checks c"
    query = f"{limits} ON c.id = p.check_id JOIN reference_revisions r ON r.id = c.revision_id"
    assert await fetch(f"{query} WHERE r.number = 2") == [(D("1"), D("5"))]


#: Значение `92` в редакции 2 текстом базы: порог и доля.
WRITTEN = {
    "param:max_seconds": "SELECT p.value::text FROM reference_params p JOIN reference_checks c"
    " ON c.id = p.check_id JOIN reference_revisions r ON r.id = c.revision_id WHERE r.number = 2",
    "share_percent": "SELECT c.share_percent::text FROM reference_checks c JOIN reference_revisions"
    " r ON r.id = c.revision_id WHERE r.number = 2 AND c.code = '92'",
}


@pytest.mark.parametrize(
    ("field", "value", "line", "written"),
    [
        ("param:max_seconds", D("3.5" + "0" * 16400), "max_seconds 3.25 → 3.5 с", "3.5"),
        ("param:max_seconds", D("0E-16400"), "max_seconds 3.25 → 0 с", "0"),
        ("param:max_seconds", D("-0E-16400"), "max_seconds 3.25 → 0 с", "0"),
        ("param:max_seconds", D("-0"), "max_seconds 3.25 → 0 с", "0"),
        ("share_percent", D("20." + "0" * 16400), "доля: 10 % → 20 %", "20"),
    ],
    ids=["trailing-zeros", "zero-16400", "minus-zero-16400", "minus-zero", "share-trailing-zeros"],
)
async def test_a318_the_checked_value_is_written_without_trailing_zeros(
    committed: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: Decimal,
    line: str,
    written: str,
) -> None:
    """A318, сверка среза 2 (находка проверяющей 10:43Z, E10–E12): пределы `92` — 0–60 с (вставка
    с подменой таблицы единиц); годное значение порога и доли с хвостовыми нулями, ноль и «-0»
    пишутся тем числом, что сверено, — без хвостовых нулей, степени и знака у нуля: предпросмотр и
    запись — один отчёт, запись не падает, в редакции 2 — это число."""
    with monkeypatch.context() as patch:
        patch.setitem(file.UNITS, Unit.SECONDS, (D("0"), D("60")))
        await insert_all(ONE)
    change = ("92", field, value)
    report = [f"92 · {line}", "редакция 1 → 2"]
    assert [await edit(committed, change, apply=a) for a in (False, True)] == [report, report]
    assert await fetch(WRITTEN[field]) == [(written,)]


async def test_a313_an_edit_failure_before_commit_rolls_its_session_back(
    committed: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A313 у правки: вставка строки падает (подмена) — «сбой записи: <тип> — справочник не
    тронут», текста исключения нет; сессия правки откатана, редакция по-прежнему одна."""
    await insert_all(ONE)

    async def fails(*args: Any) -> int:
        raise RuntimeError("подмена: строка 92")

    monkeypatch.setattr(store, "insert_row", fails)
    with pytest.raises(store.WriteFailedError) as failed:
        await edit(committed, *CHANGES)
    assert (str(failed.value), await numbers()) == (
        "сбой записи: RuntimeError — справочник не тронут",
        [(1, "active")],
    )


async def test_a319_an_unseen_revision_or_an_empty_reference_is_refused(
    committed: AsyncEngine,
) -> None:
    """A319: правка при пустом справочнике — «справочник не загружен…»; правка с `expected=1`
    при активной 2 — «справочник уже изменён: вы смотрели 1, активна 2 — перечитайте»."""
    assert await refused(committed, *CHANGES) == [store.NOT_LOADED]
    await insert_all(
        [{"rows": SAMPLE, "number": 1, "status": "archived"}, {"rows": SAMPLE, "number": 2}]
    )
    assert await refused(committed, *CHANGES) == [STALE.format(1, 2)]
    assert await numbers() == [(1, "archived"), (2, "active")]


async def test_a319_two_edits_wait_for_the_lock_and_one_writes(committed: AsyncEngine) -> None:
    """A319: замок держит соединение теста; ждут его две правки с `expected=1` (видно в
    `pg_locks`); замок снят — редакцию дала ровно одна, вторая — «уже изменён»; номера подряд."""
    async with holding(SAMPLE):
        tasks = [
            asyncio.create_task(edit(committed, ("92", "param:max_seconds", D(value))))
            for value in ("3.5", "4")
        ]
        assert await dbtools.lock_waiters(locks.REFERENCE, at_least=2) == 2
    done = await asyncio.gather(*tasks, return_exceptions=True)
    outcome = sorted(
        d.reasons if isinstance(d, file.FileRefusedError) else ["записала"] for d in done
    )
    assert outcome == [["записала"], [STALE.format(1, 2)]]
    assert await numbers() == [(1, "archived"), (2, "active")]


async def test_a319_an_edit_and_a_load_wait_and_one_writes(committed: AsyncEngine) -> None:
    """A319: замок держит соединение теста; ждут его правка с `expected=1` и `load Ф2 --expect
    Т(1)`; замок снят — редакцию дала ровно одна запись, вторая — свой отказ: «уже изменён» у
    правки, «изменился после проверки» у загрузки; активна одна, номера подряд."""
    found = file.parse(F2)
    async with holding(SAMPLE):
        tasks = [
            asyncio.create_task(edit(committed, ("92", "param:max_seconds", D("3.5")))),
            asyncio.create_task(store.load(committed, found, expect=(found.sha256, 1), reason="п")),
        ]
        assert await dbtools.lock_waiters(locks.REFERENCE, at_least=2) == 2
    done = await asyncio.gather(*tasks, return_exceptions=True)
    outcome = [d.reasons if isinstance(d, file.FileRefusedError) else "записала" for d in done]
    assert outcome in (["записала", [CHANGED.format(1, 2)]], [[STALE.format(1, 2)], "записала"])
    assert await numbers() == [(1, "archived"), (2, "active")]
