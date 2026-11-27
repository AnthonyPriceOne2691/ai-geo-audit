"""Наборы в базе (Ч2.2): встроенный набор «Все проверки» — `ensure_all_checks` (A334).

Запись — по образцу записи справочника Ч2.1б: одна транзакция в сессии вызывающего, первыми
запросами — предел ожидания и замок `CHECK_SETS`; не дождались — отказ по коду 55P03. Фиксация —
явная, в своей ветке: сбой до неё — «наборы не тронуты», на ней — «запись могла пройти». Ревизия
`0004` заводит встроенный набор сама; функция — для базы, где его нет: оснастка тестов после
очистки, затем записи Ч2.2б.
"""

from __future__ import annotations

from typing import Final

from sqlalchemy import func, insert, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_geo_audit.sets.models import CheckSet, CheckSetVersion, SetKind, VersionStatus
from ai_geo_audit.storage import locks

#: Имя встроенного набора — тем же словом его заводит ревизия `0004`.
ALL_CHECKS: Final = "Все проверки"
#: Предел ожидания замка записи, с — константа без настройки: правка — через ревью.
LOCK_WAIT_SECONDS = 10
BUSY: Final = "наборы сейчас пишет другая запись: ждали {} с — повторите позже"
_LOCK_NOT_AVAILABLE: Final = "55P03"


class SetRefusedError(ValueError):
    """Отказ наборов словами: что не так и что делать."""


class WriteFailedError(RuntimeError):
    """Сбой до фиксации: транзакция откатилась, наборы не тронуты (A334)."""


class CommitFailedError(RuntimeError):
    """Сбой на фиксации: запись могла пройти, повтор безопасен (A334)."""


async def ensure_all_checks(session: AsyncSession) -> bool:
    """Встроенный набор, если его нет: вид `all_checks`, версия 1 активна, состава нет. Одна
    транзакция в сессии вызывающего — до вызова в ней не пишут: замок, поиск после него, запись и
    явная фиксация; записан — `True`. Набор есть — транзакция закрывается без записи, `False`.
    Отказ замка и сбой до фиксации откатывают сессию, сбой на фиксации — `CommitFailedError`;
    тексты исключений в них не идут (L57)."""
    try:
        await _lock(session)
        found = select(CheckSet.id).where(CheckSet.kind == SetKind.ALL_CHECKS)
        if await session.scalar(found) is not None:
            await session.rollback()
            return False
        new = insert(CheckSet).values(name=ALL_CHECKS, kind=SetKind.ALL_CHECKS)
        set_id = await session.scalar(new.returning(CheckSet.id))
        version = insert(CheckSetVersion).values(
            set_id=set_id, number=1, status=VersionStatus.ACTIVE, published_at=func.now()
        )
        await session.execute(version)
    except SetRefusedError:
        await session.rollback()
        raise
    except Exception as failure:
        await session.rollback()
        kind = type(failure).__name__
        raise WriteFailedError(f"сбой записи: {kind} — наборы не тронуты, повторите") from failure
    try:
        await session.commit()
    except Exception as failure:
        kind = type(failure).__name__
        raise CommitFailedError(
            f"сбой на фиксации: {kind} — запись могла пройти; повтор безопасен — второго"
            " встроенного набора не будет"
        ) from failure
    return True


async def _lock(session: AsyncSession) -> None:
    """Первые запросы транзакции: предел ожидания и замок `CHECK_SETS`; не дождались — отказ по
    коду 55P03, как у записи справочника."""
    limit = LOCK_WAIT_SECONDS
    setting = text("SELECT set_config('lock_timeout', :value, true)")
    await session.execute(setting, {"value": f"{limit}s"})
    try:
        await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": locks.CHECK_SETS})
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) != _LOCK_NOT_AVAILABLE:
            raise
        raise SetRefusedError(BUSY.format(limit)) from None
