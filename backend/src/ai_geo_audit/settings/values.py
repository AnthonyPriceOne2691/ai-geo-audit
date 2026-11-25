"""Обычные настройки в базе (A232–A235, A238): «новая версия, а не правка» (b1:430).

`current` — значение последней версии или умолчание реестра с версией 0. Значение из базы проходит
ту же проверку, что и запись: негодное — `SettingInvalid` и запись ERROR, умолчанием не подменяется
(A235). `put_setting` — проверка по реестру и вставка версии «последняя + 1» под точкой
сохранения: после отказа транзакция вызывающего жива. То же значение, что сейчас, — без записи и без
отказа, даже со старым `expected` (A232). Две записи одной версии разом — отказ по имени
ограничения, а не по тексту ошибки (A234). Фиксирует вызывающий.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Row, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ai_geo_audit.settings import registry
from ai_geo_audit.settings.models import SettingVersion

#: Один логгер пакета — его называют A223 и A235; поля записи закреплены спекой.
log = logging.getLogger("ai_geo_audit.settings")
#: Ограничение пары «имя и версия»: по его имени узнаётся запись той же версии разом (A234).
SAME_VERSION = "uq_setting_versions_name_version"
#: Значение из базы в тексте отказа — JSON до 60 знаков: список в 300 пар целиком туда не идёт.
SHOWN_MAX = 60

__all__ = [
    "SettingChanged",
    "SettingInvalid",
    "SettingRefused",
    "Versioned",
    "current",
    "put_setting",
]


class SettingRefused(ValueError):  # noqa: N818 -- имя отказа из спеки (A233)
    """Имя или значение настройки не годится: слово настройки и причина."""


class SettingChanged(RuntimeError):  # noqa: N818 -- имя отказа из спеки (A234)
    """Настройку уже изменили: правка шла от прежней версии — перечитать и повторить."""


class SettingInvalid(RuntimeError):  # noqa: N818 -- имя отказа из спеки (A235)
    """В базе негодное значение настройки: умолчанием оно не подменяется."""


@dataclass(frozen=True, slots=True)
class Versioned:
    """Значение настройки и номер его версии; 0 — строк нет, значение — умолчание реестра."""

    value: object
    version: int


def _spec(name: str) -> registry.SettingSpec:
    spec = registry.setting(name)
    if spec is None:
        raise SettingRefused(f"{name} — такой настройки нет")
    return spec


async def _latest(session: AsyncSession, name: str) -> Row[tuple[Any, int]] | None:
    latest = (
        select(SettingVersion.value, SettingVersion.version)
        .where(SettingVersion.name == name)
        .order_by(SettingVersion.version.desc())
        .limit(1)
    )
    return (await session.execute(latest)).one_or_none()


def _shown(value: object) -> str:
    text = json.dumps(value)
    return text if len(text) <= SHOWN_MAX else f"{text[: SHOWN_MAX - 1]}…"


def _changed(version: int) -> str:
    return f"настройку уже изменили: версия {version} — перечитайте"


def _constraint(error: IntegrityError) -> str | None:
    """Имя нарушенного ограничения из ответа базы (`storage.models`), не из текста ошибки."""
    return getattr(getattr(error.orig, "__cause__", None), "constraint_name", None)


async def current(session: AsyncSession, name: str) -> Versioned:
    """Значение последней версии или умолчание реестра с версией 0. Негодное в базе —
    `SettingInvalid` и запись ERROR `setting-invalid` без значения (A235)."""
    spec = _spec(name)
    row = await _latest(session, name)
    if row is None:
        return Versioned(spec.fit(spec.default), 0)
    try:
        value = spec.fit(row.value)
    except registry.Unfit as unfit:
        fields = {"event": "setting-invalid", "setting": name}
        log.exception("настройка в базе не годится: %s — %s", spec.word, unfit.found, extra=fields)
        found = f"{spec.word}: в базе {_shown(row.value)} — {unfit.found}"
        raise SettingInvalid(found) from None
    return Versioned(value, row.version)


async def put_setting(
    session: AsyncSession, name: str, value: object, *, expected: int | None = None
) -> Versioned:
    """Новая версия настройки. То же значение, что сейчас, — без записи и без отказа; иначе
    `expected` не последняя версия — `SettingChanged` до записи, та же версия разом — тот же отказ
    по имени ограничения. Сравнение — JSON-текстом: `true` в базе не равно 1."""
    spec = _spec(name)
    try:
        fitted = spec.fit(value)
    except registry.Unfit as unfit:
        raise SettingRefused(f"{spec.word} — {unfit.need}") from None
    row = await _latest(session, name)
    version, stored = (row.version, row.value) if row else (0, spec.default)
    if json.dumps(fitted) == json.dumps(stored):
        return Versioned(fitted, version)
    if expected is not None and expected != version:
        raise SettingChanged(_changed(version))
    new = insert(SettingVersion).values(name=name, version=version + 1, value=fitted)
    try:
        async with session.begin_nested():
            await session.execute(new)
    except IntegrityError as error:
        if _constraint(error) != SAME_VERSION:
            raise
        raise SettingChanged(_changed(version + 1)) from None
    return Versioned(fitted, version + 1)
