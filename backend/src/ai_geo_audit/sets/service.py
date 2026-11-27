"""Наборы в базе (Ч2.2): встроенный набор «Все проверки» — `ensure_all_checks` (A334), коды версии
для редакции справочника — `codes_of` (A334), блок AI-видимости версии — `ai_block` (A337).

Запись — по образцу записи справочника Ч2.1б: одна транзакция в сессии вызывающего, первыми
запросами — предел ожидания и замок `CHECK_SETS`; не дождались — отказ по коду 55P03. Фиксация —
явная, в своей ветке: сбой до неё — «наборы не тронуты», на ней — «запись могла пройти». Ревизия
`0004` заводит встроенный набор сама; функция — для базы, где его нет: оснастка тестов после
очистки, затем записи Ч2.2б. Чтение — одна правда состава: у встроенного набора строк нет, его коды
— вся редакция и оба каталога.
"""

from __future__ import annotations

from typing import Final

from sqlalchemy import func, insert, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_geo_audit.reference.models import ReferenceCheck, ReferenceRevision
from ai_geo_audit.sets import catalogs
from ai_geo_audit.sets.models import (
    CheckSet,
    CheckSetItem,
    CheckSetVersion,
    ItemKind,
    SetKind,
    VersionStatus,
)
from ai_geo_audit.storage import locks

#: Имя встроенного набора — тем же словом его заводит ревизия `0004`.
ALL_CHECKS: Final = "Все проверки"
#: Предел ожидания замка записи, с — константа без настройки: правка — через ревью.
LOCK_WAIT_SECONDS = 10
BUSY: Final = "наборы сейчас пишет другая запись: ждали {} с — повторите позже"
_LOCK_NOT_AVAILABLE: Final = "55P03"
NO_VERSION: Final = "версии набора с id {} нет — перечитайте наборы"
DRAFT: Final = (
    "набор «{}», версия {} — черновик, аудиту не годится: опубликуйте его или возьмите активную"
    " версию"
)
NO_REVISION: Final = "редакции справочника {} нет — возьмите активную редакцию"
NOT_IN_REVISION: Final = "кода {} нет в редакции {} — возьмите редакцию, где он есть"
NO_METRIC: Final = "метрики {} нет в каталоге — уберите её из набора черновиком"
NO_ANSWER: Final = "проверки выдачи {} нет в каталоге — уберите её из набора черновиком"
#: Блок AI-видимости словами — одно место для `describe`, экрана и отчёта (A337).
WHOLE: Final = "целиком"
PART: Final = "частично"
ABSENT: Final = "нет в наборе"
METRIC_CODES: Final = tuple(metric.code for metric in catalogs.METRICS)
ANSWER_CODES: Final = tuple(answer.code for answer in catalogs.ANSWERS)


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


async def codes_of(session: AsyncSession, version_id: int, revision_id: int) -> list[str]:
    """Коды версии для редакции справочника (A334): проверки — в порядке строк редакции, затем
    метрики и проверки выдачи — в порядке каталогов. У встроенного набора — все проверки редакции
    и оба каталога, у своего — его строки состава. Черновик, код проверки вне редакции, метрика или
    проверка выдачи вне каталога, нет версии, нет редакции — отказ словами с подсказкой; все
    причины — по строке на каждую."""
    version, owner = await _version(session, version_id)
    if version.status == VersionStatus.DRAFT:
        raise SetRefusedError(DRAFT.format(owner.name, version.number))
    revision = select(ReferenceRevision.number).where(ReferenceRevision.id == revision_id)
    number = await session.scalar(revision)
    if number is None:
        raise SetRefusedError(NO_REVISION.format(revision_id))
    rows = select(ReferenceCheck.code).where(ReferenceCheck.revision_id == revision_id)
    checks = list((await session.scalars(rows.order_by(ReferenceCheck.position))).all())
    if owner.kind == SetKind.ALL_CHECKS:
        return [*checks, *METRIC_CODES, *ANSWER_CODES]
    items = await _items(session, version_id)
    strangers = _strangers(items, checks, number)
    if strangers:
        raise SetRefusedError("\n".join(strangers))
    return [
        *(code for code in checks if code in items[ItemKind.CHECK]),
        *(code for code in METRIC_CODES if code in items[ItemKind.METRIC]),
        *(code for code in ANSWER_CODES if code in items[ItemKind.ANSWER]),
    ]


async def ai_block(session: AsyncSession, version_id: int) -> str:
    """Блок AI-видимости версии (A337) — вычисление из состава, своей правды у него нет: все 11
    метрик каталога — «целиком», часть — «частично», ни одной — «нет в наборе»; встроенный набор —
    «целиком»; нет версии — отказ."""
    _, owner = await _version(session, version_id)
    if owner.kind == SetKind.ALL_CHECKS:
        return WHOLE
    metrics = set((await _items(session, version_id))[ItemKind.METRIC]) & set(METRIC_CODES)
    if not metrics:
        return ABSENT
    return WHOLE if len(metrics) == len(METRIC_CODES) else PART


async def _version(session: AsyncSession, version_id: int) -> tuple[CheckSetVersion, CheckSet]:
    """Версия и её набор; нет версии — отказ по id: другого о ней не известно."""
    query = select(CheckSetVersion, CheckSet).join(CheckSet, CheckSet.id == CheckSetVersion.set_id)
    found = (await session.execute(query.where(CheckSetVersion.id == version_id))).first()
    if found is None:
        raise SetRefusedError(NO_VERSION.format(version_id))
    return found[0], found[1]


def _strangers(items: dict[ItemKind, list[str]], checks: list[str], number: int) -> list[str]:
    """Отказ на каждый код состава, которого нет в редакции `number` или в своём каталоге."""
    found = [NOT_IN_REVISION.format(c, number) for c in items[ItemKind.CHECK] if c not in checks]
    found += [NO_METRIC.format(c) for c in items[ItemKind.METRIC] if c not in METRIC_CODES]
    found += [NO_ANSWER.format(c) for c in items[ItemKind.ANSWER] if c not in ANSWER_CODES]
    return found


async def _items(session: AsyncSession, version_id: int) -> dict[ItemKind, list[str]]:
    """Коды строк состава версии по виду, каждый вид — по порядку кодов."""
    query = select(CheckSetItem.kind, CheckSetItem.code).where(
        CheckSetItem.version_id == version_id
    )
    found: dict[ItemKind, list[str]] = {kind: [] for kind in ItemKind}
    for kind, code in (await session.execute(query.order_by(CheckSetItem.code))).all():
        found[kind].append(code)
    return found
