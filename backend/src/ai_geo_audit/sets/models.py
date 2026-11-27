"""Таблицы наборов проверок (A320–A322): набор, версии набора и строки состава версии.

Набор — имя, описание и вид: свой (`custom`) или встроенный «Все проверки» (`all_checks`, один на
базу — частичный уникальный индекс). Версия — номер в наборе, статус, вариант моделей и знак правки
`saves`; одна активная и один черновик на набор — частичные уникальные индексы. Строка состава —
код и его вид: проверка справочника, метрика или проверка выдачи; вид кода по виду строки сверяет
проверка базы. Опубликованную версию, вид и имя набора держит триггер ревизии `0004` — отказ базы
`check_set_frozen`. Выражения проверок записаны так, как их печатает Postgres (A236).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, Identity, Index, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column

from ai_geo_audit.storage.models import Base, enum_column


class SetKind(StrEnum):
    CUSTOM = "custom"
    ALL_CHECKS = "all_checks"


class VersionStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"


class PlatformModels(StrEnum):
    """Вариант моделей: `base` — «базовый», `lite` — «с экономией» (ТЗ:451–460)."""

    BASE = "base"
    LITE = "lite"


class ItemKind(StrEnum):
    CHECK = "check"
    METRIC = "metric"
    ANSWER = "answer"


class CheckSet(Base):
    """Набор: имя — часть истории аудитов, вид и имя не меняются; описание правится."""

    __tablename__ = "check_sets"
    __table_args__ = (
        UniqueConstraint("name"),
        CheckConstraint("char_length(name) >= 1 AND char_length(name) <= 200", name="name"),
        CheckConstraint("char_length(description) <= 1000", name="description"),
        Index(
            "uq_check_sets_all_checks",
            "kind",
            unique=True,
            postgresql_where=text("kind = 'all_checks'"),
        ),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    name: Mapped[str]
    description: Mapped[str] = mapped_column(server_default="")
    kind: Mapped[SetKind] = mapped_column(enum_column(SetKind, name="kind"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class CheckSetVersion(Base):
    """Версия: `published_at` пусто ровно у черновика, `archived_at` есть ровно у архивной."""

    __tablename__ = "check_set_versions"
    __table_args__ = (
        UniqueConstraint("set_id", "number"),
        CheckConstraint("number >= 1", name="number"),
        CheckConstraint("saves >= 0", name="saves"),
        CheckConstraint(
            "(status = 'draft') = (published_at IS NULL)"
            " AND (status = 'archived') = (archived_at IS NOT NULL)",
            name="dates",
        ),
        Index(
            "uq_check_set_versions_active",
            "set_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index(
            "uq_check_set_versions_draft",
            "set_id",
            unique=True,
            postgresql_where=text("status = 'draft'"),
        ),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    set_id: Mapped[int] = mapped_column(ForeignKey(CheckSet.id, ondelete="RESTRICT"))
    number: Mapped[int]
    status: Mapped[VersionStatus] = mapped_column(enum_column(VersionStatus, name="status"))
    saves: Mapped[int] = mapped_column(server_default=text("0"))
    platform_models: Mapped[PlatformModels] = mapped_column(
        enum_column(PlatformModels, name="platform_models"), server_default=PlatformModels.BASE
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    published_at: Mapped[datetime | None]
    archived_at: Mapped[datetime | None]


class CheckSetItem(Base):
    """Строка состава: код и его вид. Вид кода сверяется только у известного вида строки —
    неизвестный вид называет своя проверка `ck_check_set_items_kind`, а не эта."""

    __tablename__ = "check_set_items"
    __table_args__ = (
        CheckConstraint(
            r"(kind <> 'check' OR code ~ '^[0-9]{1,2}(\.[0-9]{1,2})?$')"
            " AND (kind <> 'metric' OR (code ~ '^[a-z][a-z_]{2,39}$' AND code !~ '^answer_'))"
            " AND (kind <> 'answer' OR code ~ '^answer_[a-z_]{2,32}$')",
            name="code",
        ),
    )

    version_id: Mapped[int] = mapped_column(
        ForeignKey(CheckSetVersion.id, ondelete="RESTRICT"), primary_key=True
    )
    code: Mapped[str] = mapped_column(primary_key=True)
    kind: Mapped[ItemKind] = mapped_column(enum_column(ItemKind, name="kind"))
