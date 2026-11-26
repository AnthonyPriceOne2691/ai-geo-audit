"""Таблицы справочника (A300–A302): заголовок редакции, строки проверок и их пороги.

Редакция не правится: новая запись — новая редакция, прежняя неизменна, активна одна — её держит
частичный уникальный индекс. Строки и пороги любой редакции держит триггер ревизии `0003`:
`UPDATE` и `DELETE` — отказ базы с именем `reference_frozen`. У заголовка запись меняет только
статус и `archived_at`; заголовок триггер не держит. Выражения проверок записаны так, как их
печатает Postgres (`::numeric` у чисел, пара сравнений вместо `BETWEEN`): сверка проверок модели
и базы (A236) иначе краснела бы ложно — decisions, проба 3 шага 0.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Identity,
    Index,
    Numeric,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ai_geo_audit.storage.models import Base, enum_column


class RevisionStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class RevisionSource(StrEnum):
    FILE = "file"
    EDIT = "edit"


class Method(StrEnum):
    RULES = "rules"
    RULES_MODEL = "rules_model"
    MODEL = "model"
    PLATFORMS = "platforms"
    EXTERNAL = "external"


class Coverage(StrEnum):
    FULL = "full"
    PARTIAL = "partial"
    NO_DATA = "no_data"


class Importance(StrEnum):
    CRITICAL = "critical"
    IMPORTANT = "important"
    LOW = "low"


class Scope(StrEnum):
    SITE = "site"
    PAGES = "pages"
    SAMPLE = "sample"


class Unit(StrEnum):
    SECONDS = "s"
    MILLISECONDS = "ms"
    MEGABYTES = "mb"
    PERCENT = "percent"
    CHARS = "chars"
    CLICKS = "clicks"
    COUNT = "count"
    NUMBER = "number"


class ReferenceRevision(Base):
    """Заголовок редакции: номер, статус, источник и основа, файл, причина, время."""

    __tablename__ = "reference_revisions"
    __table_args__ = (
        UniqueConstraint("number"),
        CheckConstraint("number >= 1", name="number"),
        CheckConstraint(
            "(source = 'file' AND file_sha256 IS NOT NULL AND file_rows IS NOT NULL"
            " AND source_date IS NOT NULL AND file_sha256 ~ '^[0-9a-f]{64}$')"
            " OR (source = 'edit' AND file_sha256 IS NULL AND file_rows IS NULL"
            " AND source_date IS NULL)",
            name="file",
        ),
        CheckConstraint(
            "char_length(reason) <= 500 AND (source = 'edit' OR char_length(reason) >= 1)",
            name="reason",
        ),
        CheckConstraint(
            "(status = 'active' AND archived_at IS NULL)"
            " OR (status = 'archived' AND archived_at IS NOT NULL)",
            name="archived_at",
        ),
        Index(
            "uq_reference_revisions_active",
            "status",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    number: Mapped[int]
    status: Mapped[RevisionStatus] = mapped_column(enum_column(RevisionStatus, name="status"))
    source: Mapped[RevisionSource] = mapped_column(enum_column(RevisionSource, name="source"))
    based_on_id: Mapped[int | None] = mapped_column(
        ForeignKey("reference_revisions.id", ondelete="RESTRICT")
    )
    file_sha256: Mapped[str | None]
    file_rows: Mapped[int | None]
    source_date: Mapped[date | None] = mapped_column(Date)
    reason: Mapped[str] = mapped_column(server_default="")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    archived_at: Mapped[datetime | None]


class ReferenceCheck(Base):
    """Строка справочника в редакции: место, код, тексты, способ, охват, важность, область, доля."""

    __tablename__ = "reference_checks"
    __table_args__ = (
        UniqueConstraint("revision_id", "code"),
        UniqueConstraint("revision_id", "position"),
        CheckConstraint(r"code ~ '^[0-9]{1,2}(\.[0-9]{1,2})?$'", name="code"),
        CheckConstraint(
            "char_length(block) >= 1 AND char_length(block) <= 200"
            " AND char_length(name) >= 1 AND char_length(name) <= 200",
            name="names",
        ),
        CheckConstraint(
            "char_length(norm) >= 1 AND char_length(norm) <= 4000"
            " AND char_length(error) >= 1 AND char_length(error) <= 4000"
            " AND char_length(recommendation) >= 1 AND char_length(recommendation) <= 4000",
            name="texts",
        ),
        CheckConstraint(
            "(scope = 'site' AND share_percent IS NULL)"
            " OR (scope <> 'site' AND share_percent IS NOT NULL)",
            name="share_scope",
        ),
        CheckConstraint(
            "share_percent > 0::numeric AND share_percent <= 100::numeric", name="share_range"
        ),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    revision_id: Mapped[int] = mapped_column(ForeignKey(ReferenceRevision.id, ondelete="RESTRICT"))
    position: Mapped[int]
    code: Mapped[str]
    block: Mapped[str]
    name: Mapped[str]
    method: Mapped[Method] = mapped_column(enum_column(Method, name="method"))
    coverage: Mapped[Coverage] = mapped_column(enum_column(Coverage, name="coverage"))
    norm: Mapped[str]
    error: Mapped[str]
    recommendation: Mapped[str]
    importance: Mapped[Importance] = mapped_column(enum_column(Importance, name="importance"))
    scope: Mapped[Scope] = mapped_column(enum_column(Scope, name="scope"))
    share_percent: Mapped[Decimal | None] = mapped_column(Numeric)


class ReferenceParam(Base):
    """Порог строки: имя, значение, пределы единицы на момент записи, единица."""

    __tablename__ = "reference_params"
    __table_args__ = (
        UniqueConstraint("check_id", "name"),
        CheckConstraint("name ~ '^[a-z][a-z0-9_]{0,39}$'", name="name"),
        CheckConstraint("min_value < max_value", name="bounds"),
        CheckConstraint("value >= min_value AND value <= max_value", name="in_range"),
    )

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    check_id: Mapped[int] = mapped_column(ForeignKey(ReferenceCheck.id, ondelete="RESTRICT"))
    name: Mapped[str]
    value: Mapped[Decimal] = mapped_column(Numeric)
    min_value: Mapped[Decimal] = mapped_column(Numeric)
    max_value: Mapped[Decimal] = mapped_column(Numeric)
    unit: Mapped[Unit] = mapped_column(enum_column(Unit, name="unit"))
