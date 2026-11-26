"""Замок миграций и пределы ожидания (A19, A20): настоящие процессы `alembic upgrade head`."""

from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

import asyncpg
import dbtools
import pytest
from asyncpg.transaction import Transaction
from dbtools import back_to_start, finish, ini_for, version
from pydantic import ValidationError

from ai_geo_audit import config
from ai_geo_audit.storage import locks


async def connect() -> asyncpg.Connection:
    return await asyncpg.connect(dbtools._raw(dbtools.own_url()))


async def hold_lock() -> tuple[asyncpg.Connection, Transaction]:
    """Своё соединение теста берёт замок миграций в открытой транзакции и держит его."""
    conn = await connect()
    held = conn.transaction()
    await held.start()
    await conn.execute("SELECT pg_advisory_xact_lock($1)", locks.MIGRATIONS)
    return conn, held


async def version_rows() -> list[str]:
    conn = await connect()
    try:
        return [row["version_num"] for row in await conn.fetch("SELECT * FROM alembic_version")]
    finally:
        await conn.close()


def stop(proc: subprocess.Popen[str]) -> None:
    """Процесс, переживший тест, — убить: висящий `migrate` держал бы замок чужим тестам."""
    if proc.poll() is None:
        proc.kill()
        proc.communicate()


async def test_a19_upgrade_waits_for_the_lock_holder(tmp_path: Path) -> None:
    """A19: замок миграций держит соединение теста — `upgrade head` жив и ждёт, ожидание видно в
    `pg_locks`; после снятия замка код 0, в `alembic_version` одна строка."""
    ini = ini_for(tmp_path, "good")
    conn, held = await hold_lock()
    proc = dbtools.alembic_start("upgrade", "head", config=ini)
    try:
        assert await dbtools.lock_waiters(locks.MIGRATIONS) == 1
        assert proc.poll() is None
        await held.rollback()
        code, err = finish(proc)
        rows = await version_rows()
    finally:
        stop(proc)
        await conn.close()
        await back_to_start(ini)
    assert code == 0, err[-1500:]
    assert rows == ["0004"]


async def test_a19_waiter_takes_over_when_the_holder_dies(tmp_path: Path) -> None:
    """A19: соединение держателя замка убито (`pg_terminate_backend`), пока второй `migrate`
    ждёт, — ждущий берёт замок, сам применяет ревизии и выходит с кодом 0."""
    ini = ini_for(tmp_path, "good")
    conn, _ = await hold_lock()
    proc = dbtools.alembic_start("upgrade", "head", config=ini)
    try:
        assert await dbtools.lock_waiters(locks.MIGRATIONS) == 1
        killer = await connect()
        try:
            await killer.execute("SELECT pg_terminate_backend($1)", conn.get_server_pid())
        finally:
            await killer.close()
        code, err = finish(proc)
        rows = await version_rows()
    finally:
        stop(proc)
        conn.terminate()
        await back_to_start(ini)
    assert code == 0, err[-1500:]
    assert rows == ["0004"]


async def test_a19_four_upgrades_at_once_all_succeed() -> None:
    """A19: четыре `alembic upgrade head` разом по пустой схеме — все с кодом 0, ревизия применена
    один раз, в `alembic_version` одна строка. Без замка часть процессов падает с ошибкой
    целостности, и число упавших плавает."""
    await dbtools.empty_schema(dbtools.own_url())
    procs = [dbtools.alembic_start("upgrade", "head") for _ in range(4)]
    try:
        results = [finish(proc) for proc in procs]
        rows = await version_rows()
    finally:
        for proc in procs:
            stop(proc)
        await dbtools.prepare()
    assert [code for code, _ in results] == [0, 0, 0, 0], [e[-800:] for c, e in results if c]
    assert sum("Running upgrade  -> 0001" in err for _, err in results) == 1
    assert rows == [dbtools.HEAD]


async def test_a19_lock_limit_refuses_in_words() -> None:
    """A19: замок занят дольше предела ожидания (`MIGRATION_LOCK_WAIT_SECONDS=1`) — код ≠ 0
    раньше 5 с от запуска и причина «миграции уже идут»: сколько ждали и какой предел, без
    трассировки. Порча «предел таблицы поставлен до замка» дала бы отказ на 10-й секунде."""
    conn, held = await hold_lock()
    started = time.monotonic()
    proc = dbtools.alembic_start("upgrade", "head", extra={"MIGRATION_LOCK_WAIT_SECONDS": "1"})
    try:
        code, err = finish(proc)
        took = time.monotonic() - started
    finally:
        stop(proc)
        await held.rollback()
        await conn.close()
    assert code != 0
    assert took < 5, f"отказ через {took:.1f} с: предел замка не сработал"
    assert re.search(r"миграции уже идут: ждали \d+ с, предел 1 с", err), err[-1500:]
    assert "Traceback" not in err, err[-1500:]


@pytest.mark.parametrize("value", ["0", "3601"])
def test_a19_lock_wait_outside_bounds_is_refused(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """A19: `MIGRATION_LOCK_WAIT_SECONDS` 0 и 3601 — отказ с именем настройки и границами."""
    monkeypatch.setenv("MIGRATION_LOCK_WAIT_SECONDS", value)
    with pytest.raises(ValidationError) as refusal:
        config.StorageSettings()
    assert "MIGRATION_LOCK_WAIT_SECONDS" in str(refusal.value)
    assert "от 1 до 3600 с" in str(refusal.value)


@pytest.mark.parametrize(("value", "expected"), [("1", 1), ("3600", 3600), (None, 120)])
def test_a19_lock_wait_inside_bounds_starts(
    monkeypatch: pytest.MonkeyPatch, value: str | None, expected: int
) -> None:
    """A19: 1 и 3600 — старт; без настройки предел ожидания замка — 120 с."""
    if value is not None:
        monkeypatch.setenv("MIGRATION_LOCK_WAIT_SECONDS", value)
    assert config.StorageSettings().migration_lock_wait_seconds == expected


def test_a19_migrate_does_not_start_with_the_setting_out_of_bounds() -> None:
    """A19: процесс `migrate` с `MIGRATION_LOCK_WAIT_SECONDS=0` не стартует и называет настройку,
    без трассировки."""
    run = dbtools.alembic("upgrade", "head", extra={"MIGRATION_LOCK_WAIT_SECONDS": "0"})
    assert run.returncode != 0
    assert "MIGRATION_LOCK_WAIT_SECONDS" in run.stderr and "от 1 до 3600 с" in run.stderr
    assert "Traceback" not in run.stderr, run.stderr[-1500:]


def test_a19_refusal_of_a_bad_setting_does_not_repeat_the_input() -> None:
    """A19: `MIGRATION_LOCK_WAIT_SECONDS` — не число, а метка: pydantic сам повторил бы ввод в
    своём тексте. `migrate` не стартует, называет настройку, без трассировки, и метки нет во
    всём выводе. Держит порчи «причина — `str(invalid)`» и «в строку причины добавлен ввод»
    (порча N3 проверяющего)."""
    mark = "lockwait-mark-7d1f"
    run = dbtools.alembic("upgrade", "head", extra={"MIGRATION_LOCK_WAIT_SECONDS": mark})
    out = run.stdout + run.stderr
    assert run.returncode != 0
    assert "MIGRATION_LOCK_WAIT_SECONDS" in out, out[-1500:]
    assert "Traceback" not in out, out[-1500:]
    assert mark not in out, out[-1500:]


def test_a19_lock_keys_come_from_one_registry_and_differ() -> None:
    """A19: ключ замка миграций — из реестра; ключи реестра различны. Что `env.py` берёт именно
    этот ключ, держат тесты с держателем замка: другой ключ не ждал бы держателя."""
    assert locks.REGISTRY["migrations"] == locks.MIGRATIONS
    assert len(set(locks.REGISTRY.values())) == len(locks.REGISTRY)


async def test_a20_revision_gives_up_on_a_table_held_by_another_transaction(
    tmp_path: Path,
) -> None:
    """A20: таблицу, которую меняет ревизия-проба, держит чужая открытая транзакция, —
    `upgrade` выходит с кодом ≠ 0 не раньше 10 с и не позже 15 с, причина словами, без
    трассировки и кода 55P03; база на прежней ревизии. Ждёт 10 с: предел таблицы — константа без
    настройки."""
    ini = ini_for(tmp_path, "locked_table")
    other = await connect()
    try:
        await other.execute("CREATE TABLE probe_target (id bigint PRIMARY KEY)")
        held = other.transaction()
        await held.start()
        await other.execute("SELECT count(*) FROM probe_target")
        started = time.monotonic()
        code, err = finish(dbtools.alembic_start("upgrade", "head", config=ini))
        took = time.monotonic() - started
        current = await version()
        await held.rollback()
    finally:
        await other.close()
        await back_to_start(ini)
    assert code != 0
    assert 10 <= took <= 15, f"отказ через {took:.1f} с, а не по пределу таблицы 10 с"
    assert "таблицу держит другая транзакция дольше 10 с" in err, err[-1500:]
    assert "Traceback" not in err, err[-1500:]
    assert "55P03" not in err
    assert current == dbtools.HEAD


async def test_a20_revisions_run_under_the_table_limit(tmp_path: Path) -> None:
    """A20: ревизия-проба читает `current_setting('lock_timeout')` при пределе замка по умолчанию
    (120 с) — внутри ревизии `10s`: на время ревизий стоит предел таблицы, а не предел замка."""
    ini = ini_for(tmp_path, "reads_lock_timeout")
    try:
        code, err = finish(dbtools.alembic_start("upgrade", "head", config=ini))
        conn = await connect()
        try:
            value = await conn.fetchval("SELECT value FROM probe_setting")
        finally:
            await conn.close()
    finally:
        await back_to_start(ini)
    assert code == 0, err[-1500:]
    assert value == "10s"
