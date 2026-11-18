"""Миграции: цепочка ревизий (A17)."""

from __future__ import annotations

import logging
from pathlib import Path

import asyncpg
import dbtools
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import BigInteger, Column, Index, MetaData, Table, Text, text
from sqlalchemy.ext.asyncio import AsyncEngine

from ai_geo_audit import config
from ai_geo_audit.storage import models, revisions

VERSIONS = Path(__file__).resolve().parents[1] / "migrations" / "versions"
CMP = "cmp_probe"


def write(directory: Path, name: str, number: str, down: str | None) -> None:
    down_text = f'"{down}"' if down else "None"
    body = f'revision: str = "{number}"\ndown_revision: str | None = {down_text}\n'
    (directory / name).write_text(body, encoding="utf-8")


def test_a17_chain_in_order_passes(tmp_path: Path) -> None:
    """A17: честная цепочка `0001` → `0002` проходит."""
    write(tmp_path, "0001_start.py", "0001", None)
    write(tmp_path, "0002_next.py", "0002", "0001")
    assert revisions.chain_problems(tmp_path) == []


def test_a17_two_revisions_with_one_number_are_named(tmp_path: Path) -> None:
    """A17: две ветки принесли по `0002` — красная с номером и именами обоих файлов."""
    write(tmp_path, "0001_start.py", "0001", None)
    write(tmp_path, "0002_a.py", "0002", "0001")
    write(tmp_path, "0002_b.py", "0002", "0001")
    assert revisions.chain_problems(tmp_path) == [
        "номер 0002 у нескольких ревизий: 0002_a.py, 0002_b.py"
    ]


def test_a17_a_skipped_number_is_named(tmp_path: Path) -> None:
    """A17: после `0001` идёт `0003` с `down_revision` `0001` — alembic молчит, проверка нет."""
    write(tmp_path, "0001_start.py", "0001", None)
    write(tmp_path, "0003_jump.py", "0003", "0001")
    assert revisions.chain_problems(tmp_path) == [
        "номер 0002 пропущен",
        "у 0003 down_revision 0001 вместо 0002",
    ]


def test_a17_no_revisions_refuse(tmp_path: Path) -> None:
    """A17: каталог без ревизий — отказ, а не «цепочка цела»."""
    with pytest.raises(models.NothingToCheck, match="ревизий не найдено"):
        revisions.chain_problems(tmp_path)


def test_a17_the_real_chain_is_whole() -> None:
    """A17: настоящий каталог ревизий проходит ту же проверку цепочки."""
    assert revisions.chain_problems(VERSIONS) == []


def test_a18_alembic_env_is_built_from_scratch(monkeypatch: pytest.MonkeyPatch) -> None:
    """A22: подпроцесс alembic получает адрес своей тестовой базы, `ENV_FILE=/dev/null` и
    `COVERAGE_*`; чужой `DATABASE_URL` и посторонний секрет окружения теста до него не доходят."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@127.0.0.1:5432/somebody_else")
    monkeypatch.setenv("COVERAGE_PROCESS_START", "/tmp/coverage.ini")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-for-alembic")
    env = dbtools.alembic_env()
    assert env["DATABASE_URL"] == dbtools.own_url()
    assert (env["ENV_FILE"], env["COVERAGE_PROCESS_START"]) == ("/dev/null", "/tmp/coverage.ini")
    assert set(env) == {"PATH", "ENV_FILE", "DATABASE_URL", "PYTHONUTF8", "COVERAGE_PROCESS_START"}


async def test_a22_test_database_is_built_by_upgrade_head() -> None:
    """A22: тестовая база построена `alembic upgrade head`: версия цепочки — `0001`."""
    conn = await asyncpg.connect(dbtools._raw(dbtools.own_url()))
    try:
        assert await conn.fetchval("SELECT version_num FROM alembic_version") == "0001"
    finally:
        await conn.close()


def test_a18_alembic_refuses_a_database_without_the_test_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A18: помощник получил адрес базы без префикса `audit_test_` — отказ до подпроцесса."""
    monkeypatch.setattr(dbtools, "own_name", lambda root=dbtools.ROOT: "audit")
    with pytest.raises(dbtools.RefusedError, match="«audit» не тронута"):
        dbtools.alembic("downgrade", "base")


def cmp_table(metadata: MetaData, *extra: Column | Index) -> Table:
    id_, name = Column("id", BigInteger, primary_key=True), Column("name", Text)
    return Table(CMP, metadata, id_, name, *extra)


async def diffs(engine: AsyncEngine, metadata: MetaData) -> list[str]:
    """Расхождения моделей с базой — `compare_metadata` с типами и умолчаниями, по `public`."""

    def run(sync_conn: object) -> list[str]:
        opts = {"compare_type": True, "compare_server_default": True}
        context = MigrationContext.configure(sync_conn, opts=opts)  # type: ignore[arg-type]
        return [str(d[0]) for d in compare_metadata(context, metadata)]

    async with engine.connect() as conn:
        return await conn.run_sync(run)


def test_a17_one_head_and_second_upgrade_changes_nothing() -> None:
    """A17: голова ревизий одна — `0001`; второй `upgrade head` ничего не меняет, код 0."""
    heads = ScriptDirectory.from_config(Config(str(dbtools.BACKEND / "alembic.ini"))).get_heads()
    assert heads == ["0001"]
    again = dbtools.alembic("upgrade", "head")
    assert (again.returncode, "Running upgrade" in again.stderr) == (0, False)


async def test_a17_models_match_the_database_and_the_comparison_can_go_red(
    engine: AsyncEngine,
) -> None:
    """A17: модели сервиса совпадают с базой; лишняя колонка, лишний индекс и таблица без
    модели дают расхождение — сверка умеет краснеть. Пробы схемы `probe` она не видит."""
    assert await diffs(engine, models.Base.metadata) == []
    async with engine.begin() as conn:
        await conn.execute(text(f"CREATE TABLE {CMP} (id bigint PRIMARY KEY, name text)"))
    try:
        exact, column, index = MetaData(), MetaData(), MetaData()
        cmp_table(exact)
        cmp_table(column, Column("extra", Text))
        cmp_table(index, Index("ix_cmp_probe_name", "name"))
        assert await diffs(engine, exact) == []
        assert await diffs(engine, column) == ["add_column"]
        assert await diffs(engine, index) == ["add_index"]
        assert await diffs(engine, MetaData()) == ["remove_table"]  # таблица есть, модели нет
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP TABLE {CMP}"))


def test_a17_migration_in_process_keeps_application_logging(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A17: после `upgrade head` в том же процессе запись логгера приложения доходит до вывода:
    `env.py` логгеры не выключает."""
    app = logging.getLogger("ai_geo_audit.after_migration")
    monkeypatch.setattr(config, "storage", config.StorageSettings(database_url=dbtools.own_url()))
    command.upgrade(Config(str(dbtools.BACKEND / "alembic.ini")), "head")
    app.warning("журнал жив после миграции")
    assert app.disabled is False
    assert "журнал жив после миграции" in capsys.readouterr().err
