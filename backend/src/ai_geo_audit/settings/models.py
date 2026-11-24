"""Таблицы ключей и настроек (A236): `service_keys` — строка на ключ, шифротекст с номером
мастер-ключа; `setting_versions` — «новая версия, а не правка» (b1:430).

Имена ограничений — по соглашению `storage.models`: отказ узнаётся по имени ограничения, а не по
тексту ошибки (A234). Значения ключа в таблице нет — только шифротекст (A224); `repr` строки
ключа шифротекста не показывает. Модели вне `settings/` не импортирует никто (A229).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, Identity, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ai_geo_audit.storage.models import Base


class ServiceKey(Base):
    """Ключ внешнего сервиса: шифротекст, номер мастер-ключа, отпечаток, подсказка и время."""

    __tablename__ = "service_keys"
    __table_args__ = (
        CheckConstraint("name ~ '^[a-z][a-z0-9_]{1,62}$'", name="name"),
        CheckConstraint("master_id ~ '^[0-9a-f]{8}$'", name="master_id"),
        CheckConstraint("fingerprint ~ '^[0-9a-f]{12}$'", name="fingerprint"),
        CheckConstraint("char_length(hint) = 4", name="hint"),
    )

    name: Mapped[str] = mapped_column(primary_key=True)
    ciphertext: Mapped[str]
    master_id: Mapped[str]
    fingerprint: Mapped[str]
    hint: Mapped[str | None]
    set_at: Mapped[datetime] = mapped_column(server_default=func.now())

    def __repr__(self) -> str:
        """Имя и номер мастер-ключа — без шифротекста и подсказки."""
        return f"ServiceKey(name={self.name!r}, master_id={self.master_id!r})"


class SettingVersion(Base):
    """Версия обычной настройки: имя, номер версии подряд с 1 и значение JSON."""

    __tablename__ = "setting_versions"
    __table_args__ = (
        UniqueConstraint("name", "version"),
        CheckConstraint("version >= 1", name="version"),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    name: Mapped[str]
    version: Mapped[int]
    value: Mapped[Any] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
