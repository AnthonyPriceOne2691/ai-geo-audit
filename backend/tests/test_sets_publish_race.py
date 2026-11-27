"""Правка черновика и его публикация на двух соединениях (A322; находка второй проверяющей
2026-11-27T14:27Z): публикация ждёт незафиксированную правку строк черновика — видно в `pg_locks`;
после обеих фиксаций состав тот же, что видела публикация; с пределом ожидания — отказ 55P03,
версия остаётся черновиком. Набор и черновик ставят сырые вставки с фиксацией; после теста оснастка
возвращает базу после ревизии."""

from __future__ import annotations

import asyncio
import time

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine
from test_sets_models import connect

PUBLISH = "UPDATE check_set_versions SET status = 'active', published_at = now() WHERE id = $1"
CODES = "SELECT array_agg(code ORDER BY code) FROM check_set_items WHERE version_id = $1"
STATE = f"SELECT status, ({CODES}) FROM check_set_versions WHERE id = $1"
#: Правка строки черновика: команда, состав до неё и после.
EDITS = {
    "insert": ("INSERT INTO check_set_items VALUES ($1, '91', 'check')", [], ["91"]),
    "delete": ("DELETE FROM check_set_items WHERE version_id = $1", ["92"], []),
}


async def draft(codes: list[str]) -> int:
    """Набор «Входящий клиент» и черновик версии 1 со строками `codes` — с фиксацией."""
    conn = await connect()
    try:
        new_set = "INSERT INTO check_sets (name, kind) VALUES ('Входящий клиент', 'custom')"
        set_id = await conn.fetchval(new_set + " RETURNING id")
        new = "INSERT INTO check_set_versions (set_id, number, status) VALUES ($1, 1, 'draft')"
        version: int = await conn.fetchval(new + " RETURNING id", set_id)
        for code in codes:
            await conn.execute(
                "INSERT INTO check_set_items VALUES ($1, $2, 'check')", version, code
            )
        return version
    finally:
        await conn.close()


async def state(version: int) -> tuple[str, list[str]]:
    conn = await connect()
    try:
        status, codes = await conn.fetchrow(STATE, version)
        return status, list(codes or [])
    finally:
        await conn.close()


async def waits(pid: int, task: asyncio.Task[object], limit: float = 5) -> bool:
    """Ждёт ли соединение `pid` блокировку (`pg_locks`): опрос — до `limit` с или до конца задачи,
    а не сон на глаз."""
    conn = await connect()
    try:
        deadline = time.monotonic() + limit
        query = "SELECT count(*) FROM pg_locks WHERE pid = $1 AND NOT granted"
        while not task.done() and time.monotonic() < deadline:
            if await conn.fetchval(query, pid):
                return True
            await asyncio.sleep(0.05)
        return False
    finally:
        await conn.close()


async def publish(conn: asyncpg.Connection, version: int, limit: str = "") -> list[str] | str:
    """Публикация своей транзакцией: состав, который она видит сразу после себя, — или код отказа
    при пределе ожидания `limit`."""
    try:
        async with conn.transaction():
            if limit:
                await conn.execute(f"SET LOCAL lock_timeout = '{limit}'")
            await conn.execute(PUBLISH, version)
            return list(await conn.fetchval(CODES, version) or [])
    except asyncpg.LockNotAvailableError as refused:
        return str(refused.sqlstate)


@pytest.mark.parametrize(("edit", "before", "after"), list(EDITS.values()), ids=list(EDITS))
async def test_a322_publication_waits_for_an_uncommitted_edit_of_its_draft(
    committed: AsyncEngine, edit: str, before: list[str], after: list[str]
) -> None:
    """A322: на двух соединениях — правка строки состава черновика (вставка `91`, удаление `92`)
    держит транзакцию, публикация с другого соединения ждёт её — видно в `pg_locks`; после фиксации
    правки и публикации версия активна, и её состав тот же, что видела публикация."""
    version = await draft(before)
    editor, publisher = await connect(), await connect()
    try:
        hold = editor.transaction()
        await hold.start()
        await editor.execute(edit, version)
        task = asyncio.create_task(publish(publisher, version))
        waited = await waits(publisher.get_server_pid(), task)
        await hold.commit()
        seen = await task
    finally:
        await editor.close()
        await publisher.close()
    assert (waited, seen, await state(version)) == (True, after, ("active", after))


async def test_a322_publication_gives_up_on_an_uncommitted_edit_after_the_limit(
    committed: AsyncEngine,
) -> None:
    """A322: правка черновика держит транзакцию; публикация с пределом ожидания 500 мс — отказ
    55P03, версия — черновик; правка откатилась — состав пуст."""
    version = await draft([])
    editor, publisher = await connect(), await connect()
    try:
        hold = editor.transaction()
        await hold.start()
        await editor.execute(EDITS["insert"][0], version)
        refused = await publish(publisher, version, "500ms")
        await hold.rollback()
    finally:
        await editor.close()
        await publisher.close()
    assert (refused, await state(version)) == ("55P03", ("draft", []))
