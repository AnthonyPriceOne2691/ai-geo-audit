"""Коды версии и блок AI-видимости (A334 — `codes_of`, A337): встроенный набор и свой, порядок
редакции и каталогов, отказы словами с подсказкой. Редакции Ф и Ф2 и набор «Входящий клиент» ставят
сырые вставки с фиксацией; после теста оснастка возвращает базу после ревизии. Ожидания каталогов —
строками спеки из `test_sets_models.py`, не из кода."""

from __future__ import annotations

from typing import Any

import pytest
from reference_sample import SAMPLE, SAMPLE2, insert_revision
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from test_sets_models import ANSWERS, METRICS, connect

from ai_geo_audit.sets import service

SIX = [row["code"] for row in SAMPLE]
CATALOGS = [code for code, _, _ in METRICS] + [code for code, _ in ANSWERS]
#: Ф2 — `95` на первом месте: порядок редакции не совпадает с порядком кодов.
FIRST_95 = [2, 3, 4, 5, 6, 7, 1]
BUILTIN = (
    "SELECT v.id FROM check_set_versions v JOIN check_sets s ON s.id = v.set_id"
    " WHERE s.kind = 'all_checks'"
)
Items = list[tuple[str, str]]
OWN: Items = [("92", "check"), ("91", "check"), ("visibility_score", "metric")]
OWN += [("answer_mention", "answer")]


async def given(*versions: tuple[int, str, Items]) -> dict[str, int]:
    """Редакции: `r1` — Ф в архиве, `r2` — Ф2 активна; `builtin` — версия встроенного набора; набор
    «Входящий клиент» и его версии `v<номер>` — (номер, статус, строки состава): черновик, затем,
    если нужно, публикация и архив."""
    conn = await connect()
    try:
        ids = {"r1": await insert_revision(conn, SAMPLE, number=1, status="archived")}
        ids["r2"] = await insert_revision(
            conn, SAMPLE2, number=2, based_on=ids["r1"], positions=FIRST_95
        )
        ids["builtin"] = await conn.fetchval(BUILTIN)
        new_set = "INSERT INTO check_sets (name, kind) VALUES ('Входящий клиент', 'custom')"
        set_id = await conn.fetchval(new_set + " RETURNING id")
        draft = "INSERT INTO check_set_versions (set_id, number, status) VALUES ($1, $2, 'draft')"
        for number, status, items in versions:
            version = await conn.fetchval(draft + " RETURNING id", set_id, number)
            rows = [(version, code, kind) for code, kind in items]
            await conn.executemany("INSERT INTO check_set_items VALUES ($1, $2, $3)", rows)
            if status != "draft":
                publish = "UPDATE check_set_versions SET status = 'active', published_at = now()"
                await conn.execute(publish + " WHERE id = $1", version)
            if status == "archived":
                archive = "UPDATE check_set_versions SET status = 'archived', archived_at = now()"
                await conn.execute(archive + " WHERE id = $1", version)
            ids[f"v{number}"] = version
        return ids
    finally:
        await conn.close()


def pick(ids: dict[str, int], key: str | int) -> int:
    return ids[key] if isinstance(key, str) else key


async def codes(engine: AsyncEngine, version: int, revision: int) -> list[str] | str:
    """Коды версии — или текст отказа."""
    async with AsyncSession(engine, expire_on_commit=False) as session:
        try:
            return await service.codes_of(session, version, revision)
        except service.SetRefusedError as refused:
            return str(refused)


async def block(engine: AsyncEngine, version: int) -> str:
    """Блок AI-видимости версии — или текст отказа."""
    async with AsyncSession(engine, expire_on_commit=False) as session:
        try:
            return await service.ai_block(session, version)
        except service.SetRefusedError as refused:
            return str(refused)


async def test_a334_builtin_set_takes_every_check_of_the_given_revision(
    committed: AsyncEngine,
) -> None:
    """A334: встроенный набор — `codes_of(версия, редакция 1)`: шесть проверок Ф в порядке файла,
    затем 11 метрик и 7 проверок выдачи в порядке каталогов; `codes_of(версия, 2)` — семь проверок
    Ф2, с `95`, и те же каталоги; снова редакция 1 — шесть: берётся переданная редакция, а не
    активная."""
    ids = await given()
    found = [await codes(committed, ids["builtin"], ids[key]) for key in ("r1", "r2", "r1")]
    assert found == [SIX + CATALOGS, ["95", *SIX, *CATALOGS], SIX + CATALOGS]


#: Состав против порядка редакции Ф2 и каталогов — и он же в их порядке.
SHUFFLED: Items = [
    ("92", "check"),
    ("95", "check"),
    ("91", "check"),
    ("platform_coverage", "metric"),
    ("visibility_score", "metric"),
    ("answer_as_source", "answer"),
    ("answer_recommendation", "answer"),
]
IN_ORDER = [
    "95",
    "91",
    "92",
    "visibility_score",
    "platform_coverage",
    "answer_recommendation",
    "answer_as_source",
]
ORDERS: dict[str, tuple[str, Items, str, list[str]]] = {
    "spec": ("active", OWN, "r1", ["91", "92", "visibility_score", "answer_mention"]),
    "archived": ("archived", OWN, "r1", ["91", "92", "visibility_score", "answer_mention"]),
    "against-the-orders": ("active", SHUFFLED, "r2", IN_ORDER),
}


@pytest.mark.parametrize(
    ("status", "items", "revision", "expected"), list(ORDERS.values()), ids=list(ORDERS)
)
async def test_a334_own_version_gives_its_items_in_revision_then_catalog_order(
    committed: AsyncEngine, status: str, items: Items, revision: str, expected: list[str]
) -> None:
    """A334: версия 1 набора «Входящий клиент» с составом `92`, `91`, `visibility_score`,
    `answer_mention` — `codes_of(версия, 1)`: `91`, `92` — в порядке редакции, затем
    `visibility_score`, `answer_mention`; архивная версия — те же коды; состав против порядка
    редакции Ф2 и каталогов — в их порядке."""
    ids = await given((1, status, items))
    assert await codes(committed, ids["v1"], ids[revision]) == expected


DRAFT = (
    "набор «Входящий клиент», версия 2 — черновик, аудиту не годится: опубликуйте его или возьмите"
    " активную версию"
)
NOT_95 = "кода 95 нет в редакции 1 — возьмите редакцию, где он есть"
MAGIC = "метрики magic_metric нет в каталоге — уберите её из набора черновиком"
REFUSALS: dict[str, tuple[Items, str | int, str | int, str]] = {
    "code-95": ([("95", "check")], "v1", "r1", NOT_95),
    "magic-metric": ([("magic_metric", "metric")], "v1", "r1", MAGIC),
    "magic-answer": (
        [("answer_magic", "answer")],
        "v1",
        "r1",
        "проверки выдачи answer_magic нет в каталоге — уберите её из набора черновиком",
    ),
    "every-reason": (
        [("95", "check"), ("magic_metric", "metric")],
        "v1",
        "r1",
        f"{NOT_95}\n{MAGIC}",
    ),
    "draft": (OWN, "v2", "r1", DRAFT),
    "no-version": (OWN, 999, "r1", "версии набора с id 999 нет — перечитайте наборы"),
    "no-revision": (OWN, "builtin", 99, "редакции справочника 99 нет — возьмите активную редакцию"),
}


@pytest.mark.parametrize(
    ("items", "version", "revision", "expected"), list(REFUSALS.values()), ids=list(REFUSALS)
)
async def test_a334_codes_of_refuses_in_words_with_what_to_do(
    committed: AsyncEngine, items: Items, version: str | int, revision: str | int, expected: str
) -> None:
    """A334: каждый отдельно — версия с `95` и редакция 1; метрика `magic_metric` и проверка выдачи
    `answer_magic` сырой вставкой; черновик — версия 2 того же набора; версии с id 999 нет;
    встроенный набор и редакции 99 нет — отказ словами с подсказкой, кодов без проверок нет; две
    причины разом — обе, по строке на каждую."""
    ids = await given((1, "active", items), (2, "draft", OWN))
    assert await codes(committed, pick(ids, version), pick(ids, revision)) == expected


ALL_METRICS: Items = [(code, "metric") for code, _, _ in METRICS]
TEN: Items = [item for item in ALL_METRICS if item[0] != "historical_dynamics"]
BLOCKS: dict[str, tuple[Items, str | int, str]] = {
    "all-metrics": (ALL_METRICS, "v1", "целиком"),
    "without-historical-dynamics": (TEN, "v1", "частично"),
    # метрика вне каталога (сырой вставкой, как устаревший код) блок целым не делает
    "ten-and-a-stranger": ([*TEN, ("magic_metric", "metric")], "v1", "частично"),
    "no-metrics": ([("91", "check"), ("answer_mention", "answer")], "v1", "нет в наборе"),
    "builtin": ([], "builtin", "целиком"),
    "no-version": ([], 999, "версии набора с id 999 нет — перечитайте наборы"),
}


@pytest.mark.parametrize(("items", "version", "expected"), list(BLOCKS.values()), ids=list(BLOCKS))
async def test_a337_ai_block_is_computed_from_the_metrics_of_the_version(
    committed: AsyncEngine, items: Items, version: str | int, expected: Any
) -> None:
    """A337: `ai_block` версий — все 11 кодов метрик — «целиком»; 10 без `historical_dynamics` —
    «частично», и с метрикой вне каталога — тоже; ни одного — «нет в наборе»; встроенный набор —
    «целиком»; версии с id 999 нет — «версии набора с id 999 нет — перечитайте наборы»."""
    ids = await given((1, "active", items))
    assert await block(committed, pick(ids, version)) == expected
