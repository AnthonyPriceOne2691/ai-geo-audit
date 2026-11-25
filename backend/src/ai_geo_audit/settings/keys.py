"""Ключи сервисов в базе (A223–A231): запись шифротекстом, снятие, резолвер в момент вызова и
состояние для экрана.

Ключ берётся только `resolve`: одна выборка колонок по имени и расшифровка — без своего кэша и без
карты сущностей сессии, поэтому смена в базе действует со следующего вызова (A227). Запись — одна
команда в транзакции вызывающего, фиксации внутри нет (A224): журнал остатка Ч1.12 встанет в ту же
транзакцию. Тот же отпечаток — строка не трогается (A226). Значения нет ни в тексте отказа, ни в
записи журнала, ни в состоянии (A237); расшифрованное значение уходит в вычистку журнала.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from ai_geo_audit.settings import crypto, registry
from ai_geo_audit.settings.crypto import KeyUnreadable
from ai_geo_audit.settings.models import ServiceKey

#: Один логгер пакета — его называют A223 и A235; поля записи закреплены спекой.
log = logging.getLogger("ai_geo_audit.settings")

__all__ = [
    "KeyMissing",
    "KeyRefused",
    "KeyState",
    "KeyUnreadable",
    "Resolved",
    "Written",
    "drop_key",
    "put_key",
    "resolve",
    "states",
]


class KeyRefused(ValueError):  # noqa: N818 -- имя отказа из спеки (A225)
    """Имя или значение ключа не годится: слово ключа и причина, без значения."""


class KeyMissing(LookupError):  # noqa: N818 -- имя отказа из спеки (A227)
    """Ключ не задан: провайдера звать нечем."""


@dataclass(frozen=True, slots=True)
class Resolved:
    """Значение ключа на время вызова и его отпечаток для журнала расхода; в `repr` значения нет."""

    value: str = field(repr=False)
    fingerprint: str


@dataclass(frozen=True, slots=True)
class KeyState:
    """Состояние ключа для экрана (A230): задан ли, подсказка, отпечаток, время, расшифровывается
    ли. Значения и шифротекста нет; у незаданного ключа поля строки пусты."""

    name: str
    word: str
    is_set: bool
    hint: str | None
    fingerprint: str | None
    set_at: datetime | None
    readable: bool | None


@dataclass(frozen=True, slots=True)
class Written:
    """Итог `put_key`: «задан» или «без изменений» и состояние ключа после записи."""

    outcome: str
    state: KeyState


def _spec(name: str) -> registry.KeySpec:
    spec = registry.key(name)
    if spec is None:
        raise KeyRefused(f"{name} — такого ключа нет")
    return spec


async def put_key(session: AsyncSession, name: str, value: str) -> Written:
    """Ключ — проверка по реестру, шифр текущей строкой файла и вставка или замена строки одной
    командой; тот же отпечаток — без записи. Фиксирует вызывающий."""
    spec = _spec(name)
    if problem := registry.key_problem(value):
        raise KeyRefused(f"{spec.word} — {problem}")
    sealed = crypto.seal(name, value)
    row = {
        "ciphertext": sealed.ciphertext,
        "master_id": sealed.master_id,
        "fingerprint": sealed.fingerprint,
        "hint": sealed.hint,
    }
    statement = insert(ServiceKey).values(name=name, **row)
    upsert = statement.on_conflict_do_update(
        index_elements=[ServiceKey.name],
        set_={**row, "set_at": func.now()},
        where=ServiceKey.fingerprint != statement.excluded.fingerprint,
    ).returning(ServiceKey.set_at)
    set_at = (await session.execute(upsert)).scalar_one_or_none()
    outcome = "задан" if set_at is not None else "без изменений"
    if set_at is None:
        current = select(ServiceKey.set_at).where(ServiceKey.name == name)
        set_at = (await session.execute(current)).scalar_one_or_none()
    state = KeyState(name, spec.word, True, sealed.hint, sealed.fingerprint, set_at, True)
    return Written(outcome, state)


async def drop_key(session: AsyncSession, name: str) -> str:
    """Снять ключ: «снят» или «не был задан». Фиксирует вызывающий."""
    _spec(name)
    gone = await session.execute(
        delete(ServiceKey).where(ServiceKey.name == name).returning(ServiceKey.name)
    )
    return "снят" if gone.scalar_one_or_none() else "не был задан"


async def resolve(session: AsyncSession, name: str) -> Resolved:
    """Значение ключа в момент вызова: выборка по имени и расшифровка, без кэша. Не задан —
    `KeyMissing`; не расшифровывается — `KeyUnreadable` и запись ERROR `key-unreadable`."""
    spec = _spec(name)
    columns = select(ServiceKey.ciphertext, ServiceKey.master_id, ServiceKey.fingerprint)
    row = (await session.execute(columns.where(ServiceKey.name == name))).one_or_none()
    if row is None:
        raise KeyMissing(f"ключ {spec.word} не задан")
    try:
        value = crypto.unseal(name, row.ciphertext, row.master_id)
    except KeyUnreadable as refusal:
        fields = {"event": "key-unreadable", "key": name, "master_id": row.master_id}
        log.exception("ключ не расшифровывается: %s", refusal, extra=fields)
        raise
    return Resolved(value, row.fingerprint)


async def states(session: AsyncSession) -> list[KeyState]:
    """Состояние каждого ключа реестра по порядку реестра; «расшифровывается ли» — настоящей
    расшифровкой со сверкой имени, а не по номеру мастер-ключа."""
    names = [spec.name for spec in registry.KEYS]
    columns = select(
        ServiceKey.name,
        ServiceKey.ciphertext,
        ServiceKey.master_id,
        ServiceKey.fingerprint,
        ServiceKey.hint,
        ServiceKey.set_at,
    )
    found = await session.execute(columns.where(ServiceKey.name.in_(names)))
    rows = {row.name: row for row in found}
    result = []
    for spec in registry.KEYS:
        row = rows.get(spec.name)
        if row is None:
            result.append(KeyState(spec.name, spec.word, False, None, None, None, None))
            continue
        readable = _opens(spec.name, row.ciphertext, row.master_id)
        result.append(
            KeyState(spec.name, spec.word, True, row.hint, row.fingerprint, row.set_at, readable)
        )
    return result


def _opens(name: str, ciphertext: str, master_id: str) -> bool:
    try:
        crypto.unseal(name, ciphertext, master_id)
    except KeyUnreadable:
        return False
    return True
