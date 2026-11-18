"""Сессия с явной фиксацией и шаг стадии на точке сохранения."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from ai_geo_audit.storage.engine import get_engine


@asynccontextmanager
async def session_scope(engine: AsyncEngine | None = None) -> AsyncIterator[AsyncSession]:
    """Сессия: фиксация на выходе, откат при ошибке.

    Записанное другим соединениям видно только после фиксации: отправка в базу (`flush`)
    сохранением не считается. Без `engine` — движок процесса; задача воркера передаёт свой
    (`task_engine`).
    """
    maker = async_sessionmaker(engine or get_engine(), expire_on_commit=False)
    async with maker() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


@asynccontextmanager
async def step(session: AsyncSession) -> AsyncIterator[None]:
    """Шаг стадии на точке сохранения: сбой шага откатывает только его.

    Ошибка шага уходит вызывающему, сессия после неё годна. Неотправленное до шага переживает его
    сбой: `begin_nested` сам отправляет его в базу до точки, а точка уходит перед первой командой
    шага. Обрыв процесса точка сохранения не переживает: сделанное цело только после фиксации
    стадии, а сохранность при обрыве — фиксация на шаг (Часть 3).
    """
    async with session.begin_nested():
        yield
