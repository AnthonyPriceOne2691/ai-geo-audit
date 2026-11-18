"""Тестовая база: своя у рабочей копии, снос схемы только под префиксом `audit_test_`.

Адрес сервера — только `TEST_DATABASE_URL`; `DATABASE_URL` тесты не читают.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
from collections.abc import AsyncIterator
from datetime import datetime
from enum import StrEnum
from pathlib import Path

import asyncpg
from sqlalchemy import CheckConstraint, ForeignKey, MetaData, func, select, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ai_geo_audit.storage import build_engine, models

PREFIX = "audit_test_"
SERVER = "postgresql+asyncpg://audit:audit@127.0.0.1:5462/postgres"
START = "docker compose -f docker-compose.dev.yml up -d db"
ROOT = Path(__file__).resolve().parents[2]
#: Пояс базы по умолчанию — не UTC: UTC соединения обязан задать наш движок (A9).
DB_TIMEZONE = "Asia/Tokyo"

#: Схема проб: сверка моделей сервиса с базой идёт по `public` и её не видит.
PROBE_SCHEMA = "probe"


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


def own_name(root: Path = ROOT) -> str:
    """Имя базы рабочей копии: префикс и 10 знаков sha256 пути её корня."""
    return PREFIX + hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:10]


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
    """База рабочей копии: создать, если нет; пояс по умолчанию; пустая схема; пробы."""
    name = own_name()
    server = await asyncpg.connect(_raw(server_url()))
    try:
        if not await server.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", name):
            with contextlib.suppress(asyncpg.DuplicateDatabaseError):
                await server.execute(f'CREATE DATABASE "{name}"')
        await server.execute(f"ALTER DATABASE \"{name}\" SET timezone TO '{DB_TIMEZONE}'")
    finally:
        await server.close()
    await empty_schema(own_url())
    engine = build_engine("test", url=own_url())
    try:
        async with engine.begin() as conn:
            await conn.run_sync(ProbeBase.metadata.create_all)
    finally:
        await engine.dispose()


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
