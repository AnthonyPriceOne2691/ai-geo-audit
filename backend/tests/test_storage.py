"""Движок, сессия и шаг стадии на настоящем Postgres (A9–A11)."""

from __future__ import annotations

import asyncio
import contextlib
import logging

import asyncpg
import dbtools
import pytest
from dbtools import probe
from sqlalchemy import func, insert, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_geo_audit import config, storage
from ai_geo_audit.logs import setup as logs
from ai_geo_audit.storage import session_scope, step

MARK = "marker-7f3a9c"
WHO = "SELECT application_name FROM pg_stat_activity WHERE pid = pg_backend_pid()"


def add(key: str) -> object:
    return insert(probe).values(key=key)


@pytest.fixture
def on_test_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """Движки процесса и задачи идут в тестовую базу, а не в базу разработки."""
    monkeypatch.setattr(config, "storage", config.StorageSettings(database_url=dbtools.own_url()))


async def test_a9_committed_row_is_seen_by_another_connection(committed: AsyncEngine) -> None:
    """A9: после выхода из session_scope строку видит второе независимое соединение."""
    async with session_scope(committed) as s:
        await s.execute(add("a9-commit"))
    assert await dbtools.rows(committed, "a9-commit") == 1


async def test_a9_error_inside_rolls_back(committed: AsyncEngine) -> None:
    """A9: исключение внутри — строки нет ни в одном соединении, ошибка дошла до вызывающего."""
    with pytest.raises(RuntimeError, match="сбой шага"):
        async with session_scope(committed) as s:
            await s.execute(add("a9-rollback"))
            raise RuntimeError("сбой шага")
    assert await dbtools.rows(committed, "a9-rollback") == 0


async def test_a9_sent_but_not_committed_is_not_saved(committed: AsyncEngine) -> None:
    """A9: отправленное без фиксации второе соединение не видит; после фиксации — видит."""
    async with session_scope(committed) as s:
        await s.execute(add("a9-flush"))
        await s.flush()
        assert await dbtools.rows(committed, "a9-flush") == 0
    assert await dbtools.rows(committed, "a9-flush") == 1


async def test_a9_killed_pool_connection_is_replaced(engine: AsyncEngine) -> None:
    """A9: соединение пула убито со стороны базы — следующий session_scope работает."""
    pooled = storage.build_engine("a9-ping", url=dbtools.own_url(), pool_size=1)
    kill = "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name = :n"
    try:
        async with session_scope(pooled) as s:
            await s.execute(select(1))
        async with engine.connect() as conn:
            killed = await conn.execute(text(kill), {"n": "ai-geo-audit:a9-ping"})
            assert killed.scalars().all() == [True]
        async with session_scope(pooled) as s:
            assert (await s.execute(select(1))).scalar_one() == 1
    finally:
        await pooled.dispose()


async def test_a9_connection_is_in_utc_whatever_the_database_default(engine: AsyncEngine) -> None:
    """A9: у базы пояс по умолчанию Asia/Tokyo, а соединение нашего движка — в UTC."""
    raw = await asyncpg.connect(dbtools._raw(dbtools.own_url()))
    try:
        assert await raw.fetchval("SHOW timezone") == dbtools.DB_TIMEZONE
    finally:
        await raw.close()
    async with engine.connect() as conn:
        assert (await conn.execute(text("SHOW timezone"))).scalar_one() == "UTC"


async def test_a9_query_values_stay_out_of_errors_and_log(
    committed: AsyncEngine, capsys: pytest.CaptureFixture[str]
) -> None:
    """A9: приметное значение упавшей вставки не попадает ни в исключение, ни в журнал."""
    logs.setup_logging(fmt="json")
    async with session_scope(committed) as s:
        await s.execute(add(MARK))
    with pytest.raises(IntegrityError) as caught:
        async with session_scope(committed) as s:
            await s.execute(add(MARK))
    logging.getLogger("audit").error("вставка упала", exc_info=caught.value)
    out = capsys.readouterr().err
    chain = (caught.value, caught.value.orig, caught.value.orig.__cause__)
    assert not [type(e).__name__ for e in chain if MARK in str(e)]
    assert MARK not in out and "uq_probe_key" in out  # значения нет, имя ограничения есть
    assert MARK not in logs.redact(f"DETAIL:  Key (key)=({MARK}) already exists.")  # вторая сетка


async def test_a9_error_caught_without_step_is_loud(committed: AsyncEngine) -> None:
    """A9: ошибку базы поймали без step(), это последняя команда — громкий отказ, не потеря."""
    async with session_scope(committed) as s:
        await s.execute(add("taken"))
    with pytest.raises(storage.AbortedTransactionError, match="поймали без step"):
        async with session_scope(committed) as s:
            await s.execute(add("work-1"))
            with contextlib.suppress(IntegrityError):
                await s.execute(add("taken"))
    assert await dbtools.rows(committed, "work-1") == 0


async def test_a9_unsent_object_with_taken_key_fails_as_itself(committed: AsyncEngine) -> None:
    """A9: неотправленный объект с занятым ключом на выходе — IntegrityError, не «без step()»."""
    async with session_scope(committed) as s:
        await s.execute(add("taken"))
    with pytest.raises(IntegrityError, match="uq_probe_key") as caught:
        async with session_scope(committed) as s:
            s.add(dbtools.Probe("taken"))
    assert "autoflush" not in str(caught.value)  # отправлено явно, а не попутно с проверкой


async def test_a9_echo_logs_the_query_without_values(
    committed: AsyncEngine, capsys: pytest.CaptureFixture[str]
) -> None:
    """A9: DB_ECHO_SQL=true — в журнале текст запроса, значений параметров нет."""
    logs.setup_logging(fmt="text")
    loud = storage.build_engine(
        "a9-echo", url=dbtools.own_url(), storage=config.StorageSettings(echo_sql=True)
    )
    try:
        async with session_scope(loud) as s:
            await s.execute(add(MARK))
    finally:
        await loud.dispose()
        logging.getLogger("sqlalchemy.engine.Engine").setLevel(logging.NOTSET)
    out = capsys.readouterr().err
    assert "INSERT INTO probe" in out
    assert MARK not in out


async def test_a10_failed_step_rolls_back_only_itself(committed: AsyncEngine) -> None:
    """A10: шаг 2 падает на уникальности — шаги 1 и 3 записаны, стадия фиксируется."""
    async with session_scope(committed) as s:
        await s.execute(add("taken"))
    async with session_scope(committed) as s:
        async with step(s):
            await s.execute(add("step-1"))
        with pytest.raises(IntegrityError):
            async with step(s):
                await s.execute(add("taken"))
        async with step(s):
            await s.execute(add("step-3"))
    assert [await dbtools.rows(committed, k) for k in ("step-1", "taken", "step-3")] == [1, 1, 1]


async def test_a10_unsent_work_before_a_step_survives_its_failure(committed: AsyncEngine) -> None:
    """A10: добавленное до шага и ещё не отправленное в базу переживает сбой шага."""
    async with session_scope(committed) as s:
        await s.execute(add("taken"))
    async with session_scope(committed) as s:
        s.add(dbtools.Probe("before-step"))
        with pytest.raises(IntegrityError):
            async with step(s):
                s.add(dbtools.Probe("taken"))
                await s.flush()
    assert await dbtools.rows(committed, "before-step") == 1


async def test_a10_stage_is_invisible_until_committed(committed: AsyncEngine) -> None:
    """A10: до фиксации стадии второе соединение не видит ни одной её строки, даже шага 1."""
    async with session_scope(committed) as s:
        async with step(s):
            await s.execute(add("unsaved-1"))
        async with step(s):
            await s.execute(add("unsaved-2"))
        assert await dbtools.rows(committed) == 0
    assert await dbtools.rows(committed) == 2


@pytest.mark.usefixtures("on_test_db")
async def test_a11_process_engine_is_one_and_named() -> None:
    """A11: движок процесса один на процесс, соединение подписано ai-geo-audit:api."""
    first, second = storage.get_engine("api"), storage.get_engine("api")
    assert first is second
    async with first.connect() as conn:
        assert (await conn.execute(text(WHO))).scalar_one() == "ai-geo-audit:api"


@pytest.mark.usefixtures("on_test_db")
async def test_a11_pool_defaults_are_small_for_tasks() -> None:
    """A11: без настроек у движка процесса пул 5 и запас 5, у движка задачи — 2 и 1."""
    process = storage.get_engine("api")
    assert (process.pool.size(), process.pool._max_overflow) == (5, 5)  # type: ignore[attr-defined]
    async with storage.task_engine() as task:
        assert (task.pool.size(), task.pool._max_overflow) == (2, 1)  # type: ignore[attr-defined]


async def _task(key: str) -> int:
    async with storage.task_engine("a11-task") as engine:
        async with session_scope(engine) as s:
            await s.execute(add(key))
            found = select(func.count()).select_from(probe).where(probe.c.key == key)
            return (await s.execute(found)).scalar_one()


async def _left(name: str) -> int:
    """Соединения с подписью `name`; закрытие соединения доходит до базы не мгновенно."""
    count = "SELECT count(*) FROM pg_stat_activity WHERE application_name = $1"
    conn = await asyncpg.connect(dbtools._raw(dbtools.own_url()))
    try:
        async with asyncio.timeout(3):
            while left := await conn.fetchval(count, name):
                await asyncio.sleep(0.05)
        return left
    finally:
        await conn.close()


@pytest.mark.usefixtures("on_test_db", "committed")
def test_a11_two_worker_tasks_each_in_its_own_loop() -> None:
    """A11: две задачи подряд, каждая в своём asyncio.run, — обе пишут и читают, соединений нет."""
    for key in ("task-1", "task-2"):
        assert asyncio.run(_task(key)) == 1
        assert asyncio.run(_left("ai-geo-audit:a11-task")) == 0


@pytest.mark.usefixtures("on_test_db")
def test_a11_process_engine_refuses_a_foreign_loop() -> None:
    """A11: движок процесса из другого цикла событий — отказ словами, а не ошибка драйвера."""

    async def take() -> object:
        return storage.get_engine("api")

    asyncio.run(take())
    with pytest.raises(storage.ForeignLoopError, match="в задаче воркера бери движок задачи"):
        asyncio.run(take())
