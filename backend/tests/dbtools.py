"""Тестовая база: своя у рабочей копии, снос схемы только под префиксом `audit_test_`.

Адрес сервера — только `TEST_DATABASE_URL`; `DATABASE_URL` тесты не читают.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import hashlib
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import AsyncIterator, Iterator
from datetime import datetime
from enum import StrEnum
from pathlib import Path

import asyncpg
import envguard
from sqlalchemy import CheckConstraint, ForeignKey, MetaData, func, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ai_geo_audit.storage import build_engine, models

PREFIX = "audit_test_"
# pragma: allowlist nextline secret
SERVER = "postgresql+asyncpg://audit:audit@127.0.0.1:5462/postgres"
START = "docker compose -f docker-compose.dev.yml up -d db"
ROOT = envguard.ROOT
BACKEND = ROOT / "backend"
#: Предел одного прогона alembic в тестах: зависший называется командой, а не висит.
ALEMBIC_TIMEOUT = 60
#: Пояс базы по умолчанию — не UTC: UTC соединения обязан задать наш движок (A9).
DB_TIMEZONE = "Asia/Tokyo"

#: Схема проб: сверка моделей сервиса с базой идёт по `public` и её не видит.
PROBE_SCHEMA = "probe"
#: База сессии вместо базы рабочей копии: копия шаблона под mutmut (`mutation_db`) или шаблон на
#: время постройки. Первая — действующая; пусто — база рабочей копии.
ACTIVE: list[str] = []


class ProbeBase(DeclarativeBase):
    """Модели проб: соглашение и карта типов сервиса, но своя схема Postgres."""

    metadata = MetaData(schema=PROBE_SCHEMA, naming_convention=models.NAMING)
    type_annotation_map = models.TYPES


class Status(StrEnum):
    NEW = "new"
    DONE = "done"


class Owner(ProbeBase):
    __tablename__ = "owner"

    id: Mapped[int] = mapped_column(primary_key=True)


class Probe(ProbeBase):
    """Строка пробы: уникальный `key`, ключ на `owner`, проверка `positive`, статус, типы."""

    __tablename__ = "probe"
    __table_args__ = (CheckConstraint("amount > 0", name="positive"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(unique=True)
    owner_id: Mapped[int | None] = mapped_column(
        ForeignKey(Owner.id, ondelete="RESTRICT"), index=True
    )
    amount: Mapped[int | None]
    note: Mapped[str | None]
    seen_at: Mapped[datetime | None]
    status: Mapped[Status | None] = mapped_column(models.enum_column(Status, name="status"))

    def __init__(self, key: str, **values: object) -> None:
        super().__init__(key=key, **values)


probe = Probe.__table__


class RefusedError(RuntimeError):
    """Обвязка отказалась трогать базу без тестового префикса."""


def named(key: str) -> str:
    """Имя тестовой базы: префикс и 10 знаков sha256 строки `key`."""
    return PREFIX + hashlib.sha256(key.encode()).hexdigest()[:10]


def own_name(root: Path | None = None) -> str:
    """Имя базы рабочей копии: префикс и 10 знаков sha256 пути её корня. Без корня в сессии с
    базой сессии (`ACTIVE`) — она: так копию шаблона видят alembic, живой сервер, фильтры
    `datname` и отказ A18."""
    if root is None and ACTIVE:
        return ACTIVE[0]
    return named(str((root or ROOT).resolve()))


@contextlib.contextmanager
def active(name: str) -> Iterator[None]:
    """`own_name()` внутри блока — `name`; после — как до него."""
    ACTIVE.insert(0, name)
    try:
        yield
    finally:
        ACTIVE.pop(0)


def lock_file(name: str) -> Path:
    return Path(tempfile.gettempdir()) / f"{name}.lock"


@contextlib.contextmanager
def locked(name: str) -> Iterator[None]:
    """Замок ОС на подготовку базы `name`: вложенные сессии A31 и `prepare()` внутри теста из
    четырёх потоков гейта мутаций не готовят одну базу разом (A283)."""
    with lock_file(name).open("a", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def server_url() -> URL:
    return make_url(os.environ.get("TEST_DATABASE_URL", SERVER))


def own_url() -> str:
    return server_url().set(database=own_name()).render_as_string(hide_password=False)


def _raw(url: URL | str) -> str:
    """Адрес для asyncpg напрямую, без драйвера SQLAlchemy в схеме."""
    url = make_url(url) if isinstance(url, str) else url
    return url.set(drivername="postgresql").render_as_string(hide_password=False)


async def unreachable() -> str | None:
    """Почему сервер тестов не отвечает, или None."""
    try:
        conn = await asyncpg.connect(_raw(server_url()), timeout=3)
    except (OSError, asyncpg.PostgresError, TimeoutError) as exc:
        return f"{type(exc).__name__}: {exc}"
    await conn.close()
    return None


async def empty_schema(url: str) -> None:
    """Снести схему — только в базе с тестовым префиксом; иначе отказ до соединения."""
    name = make_url(url).database or ""
    if not name.startswith(PREFIX):
        raise RefusedError(f"схема сносится только в базе {PREFIX}…; «{name}» не тронута")
    conn = await asyncpg.connect(_raw(url))
    try:
        await conn.execute(
            f"DROP SCHEMA IF EXISTS {PROBE_SCHEMA} CASCADE; DROP SCHEMA public CASCADE; "
            f"CREATE SCHEMA public; CREATE SCHEMA {PROBE_SCHEMA}"
        )
    finally:
        await conn.close()


async def prepare() -> None:
    """База рабочей копии: создать, если нет; пояс по умолчанию; пустая схема; цепочка
    ревизий `alembic upgrade head` — той же командой, что в бою; пробы — в схеме `probe`. База
    рабочей копии — под замком ОС; базу сессии (`ACTIVE`) не готовит никто другой."""
    name = own_name()
    with contextlib.nullcontext() if ACTIVE else locked(name):
        await _prepare(name)


async def _prepare(name: str) -> None:
    server = await asyncpg.connect(_raw(server_url()))
    try:
        if not await server.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", name):
            with contextlib.suppress(asyncpg.DuplicateDatabaseError):
                await server.execute(f'CREATE DATABASE "{name}"')
        await server.execute(f"ALTER DATABASE \"{name}\" SET timezone TO '{DB_TIMEZONE}'")
    finally:
        await server.close()
    await empty_schema(own_url())
    done = alembic("upgrade", "head")
    if done.returncode != 0:
        tail = done.stderr[-2000:]
        raise RuntimeError(f"alembic upgrade head упал, код {done.returncode}:\n{tail}")
    engine = build_engine("test", url=own_url())
    try:
        async with engine.begin() as conn:
            await conn.run_sync(ProbeBase.metadata.create_all)
    finally:
        await engine.dispose()


def alembic_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Окружение подпроцесса alembic — с нуля: адрес своей тестовой базы, без `.env`, переменные
    разработчика не доходят. `COVERAGE_*` проходят по приставке: так покрытие подпроцесса увидит
    ревизии. `extra` — настройки, которые тест задаёт явно. Адрес не своей тестовой базы — отказ
    до подпроцесса."""
    url = own_url()
    name = make_url(url).database or ""
    if not name.startswith(PREFIX):
        raise RefusedError(f"alembic в тестах идёт только по базе {PREFIX}…; «{name}» не тронута")
    env = {"PATH": os.environ.get("PATH", ""), "ENV_FILE": os.devnull, "DATABASE_URL": url}
    env["PYTHONUTF8"] = "1"  # журнал на русском при пустом окружении локали
    env.update({k: v for k, v in os.environ.items() if k.startswith("COVERAGE_")})
    return env | (extra or {})


def _alembic_command(args: tuple[str, ...], config: Path | None) -> list[str]:
    cli = Path(sys.executable).with_name("alembic")
    return [str(cli), *(["-c", str(config)] if config else []), *args]


def alembic(
    *args: str,
    config: Path | None = None,
    timeout: float = ALEMBIC_TIMEOUT,
    extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """`alembic <args>` подпроцессом из backend/ — та же команда, что в бою."""
    return subprocess.run(
        _alembic_command(args, config),
        cwd=BACKEND,
        env=alembic_env(extra),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def alembic_start(
    *args: str, config: Path | None = None, extra: dict[str, str] | None = None
) -> subprocess.Popen[str]:
    """`alembic <args>` подпроцессом без ожидания: несколько `migrate` разом, ждущий замка."""
    return subprocess.Popen(
        _alembic_command(args, config),
        cwd=BACKEND,
        env=alembic_env(extra),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def finish(proc: subprocess.Popen[str], timeout: float = ALEMBIC_TIMEOUT) -> tuple[int, str]:
    """Код выхода и журнал подпроцесса; зависший дольше предела убит и назван."""
    try:
        _, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        _, err = proc.communicate()
        raise AssertionError(f"alembic висит дольше {timeout} с:\n{err[-1500:]}") from None
    return proc.returncode, err


async def lock_waiters(
    key: int,
    at_least: int = 1,
    timeout: float = 10,  # noqa: ASYNC109 -- опрос базы
) -> int:
    """Сколько соединений своей базы ждут advisory-замок `key` (строки `pg_locks` с
    `granted=false`); ждать появления `at_least` не дольше `timeout`, а не «сном на глаз»."""
    conn = await asyncpg.connect(_raw(own_url()))
    query = (
        "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted"
        " AND database = (SELECT oid FROM pg_database WHERE datname = current_database())"
        " AND classid::bigint = $1 AND objid::bigint = $2 AND objsubid = 1"
    )
    deadline = time.monotonic() + timeout
    try:
        while True:
            found: int = await conn.fetchval(query, key >> 32, key & 0xFFFFFFFF)
            if found >= at_least or time.monotonic() > deadline:
                return found
            await asyncio.sleep(0.05)
    finally:
        await conn.close()


def ini_for(tmp_path: Path, scenario: str) -> Path:
    """`alembic.ini` с каталогом ревизий-проб поверх настоящего: команда та же, что в бою."""
    versions = f"{BACKEND / 'migrations' / 'versions'}{os.pathsep}"
    versions += str(Path(__file__).parent / "probe_revisions" / scenario)
    ini = tmp_path / "alembic.ini"
    script = BACKEND / "migrations"
    body = f"[alembic]\nscript_location = {script}\nversion_locations = {versions}\n"
    ini.write_text(body + "path_separator = os\n", encoding="utf-8")
    return ini


async def public_tables() -> set[str]:
    conn = await asyncpg.connect(_raw(own_url()))
    try:
        found = await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        return {row["tablename"] for row in found}
    finally:
        await conn.close()


async def version() -> str:
    conn = await asyncpg.connect(_raw(own_url()))
    try:
        return await conn.fetchval("SELECT version_num FROM alembic_version")
    finally:
        await conn.close()


#: Таблицы ревизий-проб: после теста их нет, что бы в нём ни случилось.
PROBE_TABLES = ("probe_rev", "probe_half", "probe_target", "probe_setting")


async def back_to_start(ini: Path) -> None:
    """Вернуть цепочку на `0001` и снести таблицы проб, что бы ни случилось в тесте."""
    alembic("downgrade", "0001", config=ini)
    conn = await asyncpg.connect(_raw(own_url()))
    try:
        await conn.execute(f"DROP TABLE IF EXISTS {', '.join(PROBE_TABLES)}")
    finally:
        await conn.close()


async def rows(engine: AsyncEngine, key: str | None = None) -> int:
    """Сколько строк пробы видит новое соединение — то есть сколько зафиксировано."""
    query = select(func.count()).select_from(probe)
    if key is not None:
        query = query.where(probe.c.key == key)
    async with engine.connect() as conn:
        return (await conn.execute(query)).scalar_one()


async def truncate(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE probe.probe, probe.owner RESTART IDENTITY"))


async def require_empty(engine: AsyncEngine) -> None:
    """Перед тестом с фиксациями проба пуста: иначе прошлый тест не убрал за собой."""
    left = await rows(engine)
    if left:
        raise AssertionError(f"в пробе {left} строк(и): прошлый тест не убрал за собой")


@contextlib.asynccontextmanager
async def outer_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Сессия во внешней транзакции теста. `create_savepoint`: фиксация и откат кода под тестом
    идут по точке сохранения и транзакцию теста не снимают; в конце она откатывается."""
    async with engine.connect() as conn:
        outer = await conn.begin()
        session = AsyncSession(
            bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        try:
            yield session
        finally:
            await session.close()
            await outer.rollback()


async def meet(barrier: asyncio.Barrier) -> None:
    """Встреча участников гонки: ждать не дольше 5 с, иначе тест висел бы вечно."""
    await asyncio.wait_for(barrier.wait(), timeout=5)
