"""Тестовая база и фикстуры: своя база, честная остановка, гонка двух соединений (A12)."""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path

import dbtools
import pytest
from dbtools import probe
from sqlalchemy import insert, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_geo_audit.storage import session_scope

BACKEND = dbtools.BACKEND
DEAD = "postgresql+asyncpg://audit:audit@127.0.0.1:1/postgres"


def test_a12_without_postgres_the_run_stops_once() -> None:
    """A12: Postgres не отвечает — одно сообщение с командой, без пропусков и трассировки."""
    run = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_config.py"],
        cwd=BACKEND,
        env={**os.environ, "TEST_DATABASE_URL": DEAD},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    out = run.stdout + run.stderr
    assert run.returncode == pytest.ExitCode.USAGE_ERROR, out
    assert out.count(dbtools.START) == 1
    assert "skipped" not in out and "passed" not in out and "Traceback" not in out


def test_a12_each_working_copy_has_its_own_database() -> None:
    """A12: у двух рабочих копий разные базы audit_test_<10 знаков sha256 пути корня>."""
    one, two = dbtools.own_name(Path("/copies/one")), dbtools.own_name(Path("/copies/two"))
    assert one != two
    assert all(re.fullmatch(r"audit_test_[0-9a-f]{10}", name) for name in (one, two))


async def test_a12_schema_is_dropped_only_under_the_test_prefix() -> None:
    """A12: база без префикса audit_test_ — снос отказывает до соединения, база цела."""
    dev = dbtools.server_url().set(database="audit").render_as_string(hide_password=False)
    with pytest.raises(dbtools.RefusedError, match="«audit» не тронута"):
        await dbtools.empty_schema(dev)


def test_a12_tests_take_their_address_from_test_database_url_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A12: DATABASE_URL на базу разработки тесты не читают — адрес только TEST_DATABASE_URL."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://audit:audit@10.0.0.9:5999/audit")
    server, own = dbtools.server_url(), make_url(dbtools.own_url())
    assert (own.host, own.port, own.database) == (server.host, server.port, dbtools.own_name())


async def test_a12_session_fixture_takes_the_codes_commit_and_rollback(engine: AsyncEngine) -> None:
    """A12: код в сессии теста фиксирует, ловит уникальность, откатывает — после теста пусто."""
    async with dbtools.outer_session(engine) as s:
        await s.execute(insert(probe).values(key="fix-1"))
        await s.commit()
        with pytest.raises(IntegrityError):
            await s.execute(insert(probe).values(key="fix-1"))
        await s.rollback()
        await s.execute(insert(probe).values(key="fix-2"))
        assert set((await s.execute(select(probe.c.key))).scalars()) == {"fix-1", "fix-2"}
    assert await dbtools.rows(engine) == 0


async def test_a12_two_connections_insert_one_key_once(committed: AsyncEngine) -> None:
    """A12: два соединения разом вставляют один ключ — одна строка, второе получает отказ."""
    barrier = asyncio.Barrier(2)

    async def racer() -> None:
        async with session_scope(committed) as s:
            await dbtools.meet(barrier)
            await s.execute(insert(probe).values(key="race"))

    results = await asyncio.gather(racer(), racer(), return_exceptions=True)
    assert sorted(type(r).__name__ for r in results) == ["IntegrityError", "NoneType"]
    assert await dbtools.rows(committed, "race") == 1


async def test_a12_committed_rows_do_not_outlive_their_test(engine: AsyncEngine) -> None:
    """A12: строки теста с фиксациями до следующего теста не доживают — проверка и уборка."""
    async with session_scope(engine) as s:
        await s.execute(insert(probe).values(key="left-behind"))
    with pytest.raises(AssertionError, match="не убрал за собой"):
        await dbtools.require_empty(engine)
    await dbtools.truncate(engine)
    await dbtools.require_empty(engine)
