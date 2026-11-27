"""Встроенный набор (A334, запись): `ensure_all_checks` после очистки — повтор, два вызова разом на
двух соединениях, замок с пределом ожидания, сбои до фиксации и на ней. Очистка — сырой `TRUNCATE`
таблиц наборов: встроенного набора нет; после теста оснастка возвращает базу после ревизии. Замок
держит соединение теста, ожидание видно в `pg_locks`."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

import dbtools
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from test_sets_models import CLEAR, ONE, connect, fetch, raw, state

from ai_geo_audit.sets import service
from ai_geo_audit.sets.models import CheckSetVersion
from ai_geo_audit.storage import locks

MP = pytest.MonkeyPatch
#: Строки набора и версии: те же ли они — `xmin` меняет любая запись строки.
WRITTEN = (
    "SELECT s.id, s.xmin::text, v.id, v.xmin::text FROM check_sets s"
    " JOIN check_set_versions v ON v.set_id = s.id ORDER BY s.id, v.id"
)
#: Сколько соединений своей базы держат advisory-замок: ключ — старшие и младшие 32 бита.
HOLDERS = (
    "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND granted"
    " AND database = (SELECT oid FROM pg_database WHERE datname = current_database())"
    " AND classid::bigint = $1 AND objid::bigint = $2 AND objsubid = 1"
)
FAILED = "сбой записи: RuntimeError — наборы не тронуты, повторите"
AT_COMMIT = (
    "сбой на фиксации: RuntimeError — запись могла пройти; повтор безопасен — второго встроенного"
    " набора не будет"
)


async def ensure(engine: AsyncEngine) -> bool:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        return await service.ensure_all_checks(session)


async def ensure_and_look(engine: AsyncEngine) -> tuple[bool, bool, int]:
    """`ensure_all_checks` и сразу после него, пока сессия открыта: в транзакции ли она и сколько
    соединений держат замок `CHECK_SETS` (порча C4 второй проверяющей)."""
    async with AsyncSession(engine, expire_on_commit=False) as session:
        written = await service.ensure_all_checks(session)
        key = locks.CHECK_SETS
        holders = await fetch_one(HOLDERS, key >> 32, key & 0xFFFFFFFF)
        return written, session.in_transaction(), holders


async def fetch_one(query: str, *args: object) -> int:
    conn = await connect()
    try:
        found: int = await conn.fetchval(query, *args)
        return found
    finally:
        await conn.close()


@contextlib.asynccontextmanager
async def holding() -> AsyncIterator[None]:
    """Соединение теста держит замок `CHECK_SETS` в своей транзакции; на выходе — снимает."""
    conn = await connect()
    try:
        lock = conn.transaction()
        await lock.start()
        await conn.execute("SELECT pg_advisory_xact_lock($1)", locks.CHECK_SETS)
        try:
            yield
        finally:
            await lock.rollback()
    finally:
        await conn.close()


async def test_a334_ensure_writes_the_builtin_set_once(committed: AsyncEngine) -> None:
    """A334: после подъёма встроенный набор есть — `ensure_all_checks` ничего не пишет; после
    очистки первый вызов пишет набор — вид `all_checks`, имя «Все проверки», версия 1 `active`,
    состав пуст, — второй подряд ничего не пишет: набор один, его строки те же. После каждого
    вызова сессия не в транзакции и замок `CHECK_SETS` никто не держит."""
    closed = (False, 0)
    after_upgrade = await fetch(WRITTEN)
    assert (await ensure_and_look(committed), await fetch(WRITTEN)) == (
        (False, *closed),
        after_upgrade,
    )
    await raw(CLEAR)
    assert (await ensure_and_look(committed), await state()) == ((True, *closed), ONE)
    written = await fetch(WRITTEN)
    again = (await ensure_and_look(committed), await state(), await fetch(WRITTEN))
    assert again == ((False, *closed), ONE, written)


async def test_a334_two_ensures_at_once_give_one_builtin_set(committed: AsyncEngine) -> None:
    """A334: после очистки замок `CHECK_SETS` (ключ реестра) держит соединение теста; два
    `ensure_all_checks` на двух соединениях ждут его — видно в `pg_locks`; замок снят — один пишет,
    второй — ничего: встроенный набор один."""
    assert locks.REGISTRY["check_sets"] == locks.CHECK_SETS
    await raw(CLEAR)
    async with holding():
        tasks = [asyncio.create_task(ensure(committed)) for _ in range(2)]
        assert await dbtools.lock_waiters(locks.CHECK_SETS, at_least=2) == 2
    assert (sorted(await asyncio.gather(*tasks)), await state()) == ([False, True], ONE)


async def test_a334_ensure_gives_up_on_a_held_lock_after_the_limit(
    committed: AsyncEngine, monkeypatch: MP
) -> None:
    """A334: после очистки замок держит соединение теста; предел ожидания 1 с (подмена константы,
    в бою — 10 с) — «наборы сейчас пишет другая запись: ждали 1 с — повторите позже», ожидание
    видно в `pg_locks`, ничего не записано; замок снят — тот же вызов пишет."""
    monkeypatch.setattr(service, "LOCK_WAIT_SECONDS", 1)
    busy = "наборы сейчас пишет другая запись: ждали 1 с — повторите позже"
    await raw(CLEAR)
    async with holding():
        task = asyncio.create_task(ensure(committed))
        waiting = await dbtools.lock_waiters(locks.CHECK_SETS)
        with pytest.raises(service.SetRefusedError) as refused:
            await task
        assert (waiting, str(refused.value), await state()) == (1, busy, [])
    assert (await ensure(committed), await state()) == (True, ONE)


async def test_a334_a_failure_before_commit_leaves_the_sets_untouched(
    committed: AsyncEngine, monkeypatch: MP
) -> None:
    """A334: после очистки вставка версии падает (подмена) — «сбой записи: RuntimeError — наборы
    не тронуты, повторите», текста исключения нет; сессия не в транзакции, набора нет — вставка
    набора откатилась; следующий вызов пишет."""
    await raw(CLEAR)
    real = AsyncSession.execute

    async def version_fails(self: AsyncSession, statement: Any, *args: Any, **kw: Any) -> Any:
        table = getattr(statement, "table", None)
        if getattr(table, "name", None) == CheckSetVersion.__tablename__:
            raise RuntimeError("подмена: сбой вставки версии")
        return await real(self, statement, *args, **kw)

    async with AsyncSession(committed, expire_on_commit=False) as session:
        with monkeypatch.context() as patch:
            patch.setattr(AsyncSession, "execute", version_fails)
            with pytest.raises(service.WriteFailedError) as failed:
                await service.ensure_all_checks(session)
        assert (str(failed.value), session.in_transaction()) == (FAILED, False)
    assert await state() == []
    assert (await ensure(committed), await state()) == (True, ONE)


async def test_a334_a_failure_at_commit_says_the_write_may_have_passed(
    committed: AsyncEngine, monkeypatch: MP
) -> None:
    """A334: после очистки фиксация проходит, затем бросок (подмена) — «сбой на фиксации:
    RuntimeError — запись могла пройти; повтор безопасен — второго встроенного набора не будет»;
    набор есть, повтор ничего не пишет."""
    await raw(CLEAR)
    real = AsyncSession.commit

    async def commit_then_fail(self: AsyncSession) -> None:
        await real(self)
        raise RuntimeError("подмена: сбой после фиксации")

    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "commit", commit_then_fail)
        with pytest.raises(service.CommitFailedError) as failed:
            await ensure(committed)
    assert (str(failed.value), await state()) == (AT_COMMIT, ONE)
    assert (await ensure(committed), await state()) == (False, ONE)
