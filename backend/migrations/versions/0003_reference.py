"""Таблицы справочника — редакции, строки проверок, пороги — и триггер их неизменности (Ч2.1, A300).

Ревизия 0003, после 0002. Создана 2026-11-26 11:06:15.818214+00:00 командой
`alembic revision --autogenerate`; функция и триггеры неизменности дописаны руками: alembic их не
видит, их держит A300 после подъёма и отката.

Соглашение: колонка, добавленная задним числом, получает NULL — «неизвестно», а не умолчание,
которое читается как измеренное значение. Номер — следующий по порядку; кто сливает вторым,
перенумеровывает свою ревизию.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | None = None
depends_on: str | None = None

#: Отказ любой правке строки или порога редакции: имя ограничения в ответе базы — `reference_frozen`
#: (проба 1 шага 0: доходит до `constraint_name` под asyncpg), текст — без значений строки.
FROZEN = """
CREATE FUNCTION reference_frozen() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'строки редакции справочника не меняются — правка идёт новой редакцией'
        USING ERRCODE = 'restrict_violation', CONSTRAINT = 'reference_frozen';
END
$$
"""


def _enum(*values: str, name: str) -> sa.Enum:
    """Перечисление, как его даёт `storage.models.enum_column`: строка 32 с проверкой."""
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True, length=32)


def upgrade() -> None:
    """Три таблицы, частичный индекс одной активной, функция и два триггера неизменности."""
    op.create_table(
        "reference_revisions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("number", sa.BigInteger(), nullable=False),
        sa.Column("status", _enum("active", "archived", name="status"), nullable=False),
        sa.Column("source", _enum("file", "edit", name="source"), nullable=False),
        sa.Column("based_on_id", sa.BigInteger(), nullable=True),
        sa.Column("file_sha256", sa.Text(), nullable=True),
        sa.Column("file_rows", sa.BigInteger(), nullable=True),
        sa.Column("source_date", sa.Date(), nullable=True),
        sa.Column("reason", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(source = 'file' AND file_sha256 IS NOT NULL AND file_rows IS NOT NULL"
            " AND source_date IS NOT NULL AND file_sha256 ~ '^[0-9a-f]{64}$')"
            " OR (source = 'edit' AND file_sha256 IS NULL AND file_rows IS NULL"
            " AND source_date IS NULL)",
            name=op.f("ck_reference_revisions_file"),
        ),
        sa.CheckConstraint(
            "(status = 'active' AND archived_at IS NULL)"
            " OR (status = 'archived' AND archived_at IS NOT NULL)",
            name=op.f("ck_reference_revisions_archived_at"),
        ),
        sa.CheckConstraint(
            "char_length(reason) <= 500 AND (source = 'edit' OR char_length(reason) >= 1)",
            name=op.f("ck_reference_revisions_reason"),
        ),
        sa.CheckConstraint("number >= 1", name=op.f("ck_reference_revisions_number")),
        sa.ForeignKeyConstraint(
            ["based_on_id"],
            ["reference_revisions.id"],
            name=op.f("fk_reference_revisions_based_on_id_reference_revisions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reference_revisions")),
        sa.UniqueConstraint("number", name=op.f("uq_reference_revisions_number")),
    )
    op.create_index(
        "uq_reference_revisions_active",
        "reference_revisions",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_table(
        "reference_checks",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("revision_id", sa.BigInteger(), nullable=False),
        sa.Column("position", sa.BigInteger(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("block", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "method",
            _enum("rules", "rules_model", "model", "platforms", "external", name="method"),
            nullable=False,
        ),
        sa.Column("coverage", _enum("full", "partial", "no_data", name="coverage"), nullable=False),
        sa.Column("norm", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("recommendation", sa.Text(), nullable=False),
        sa.Column(
            "importance", _enum("critical", "important", "low", name="importance"), nullable=False
        ),
        sa.Column("scope", _enum("site", "pages", "sample", name="scope"), nullable=False),
        sa.Column("share_percent", sa.Numeric(), nullable=True),
        sa.CheckConstraint(
            "(scope = 'site' AND share_percent IS NULL)"
            " OR (scope <> 'site' AND share_percent IS NOT NULL)",
            name=op.f("ck_reference_checks_share_scope"),
        ),
        sa.CheckConstraint(
            r"code ~ '^[0-9]{1,2}(\.[0-9]{1,2})?$'", name=op.f("ck_reference_checks_code")
        ),
        sa.CheckConstraint(
            "char_length(block) >= 1 AND char_length(block) <= 200"
            " AND char_length(name) >= 1 AND char_length(name) <= 200",
            name=op.f("ck_reference_checks_names"),
        ),
        sa.CheckConstraint(
            "char_length(norm) >= 1 AND char_length(norm) <= 4000"
            " AND char_length(error) >= 1 AND char_length(error) <= 4000"
            " AND char_length(recommendation) >= 1 AND char_length(recommendation) <= 4000",
            name=op.f("ck_reference_checks_texts"),
        ),
        sa.CheckConstraint(
            "share_percent > 0::numeric AND share_percent <= 100::numeric",
            name=op.f("ck_reference_checks_share_range"),
        ),
        sa.ForeignKeyConstraint(
            ["revision_id"],
            ["reference_revisions.id"],
            name=op.f("fk_reference_checks_revision_id_reference_revisions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reference_checks")),
        sa.UniqueConstraint(
            "revision_id", "code", name=op.f("uq_reference_checks_revision_id_code")
        ),
        sa.UniqueConstraint(
            "revision_id", "position", name=op.f("uq_reference_checks_revision_id_position")
        ),
    )
    op.create_table(
        "reference_params",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("check_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("value", sa.Numeric(), nullable=False),
        sa.Column("min_value", sa.Numeric(), nullable=False),
        sa.Column("max_value", sa.Numeric(), nullable=False),
        sa.Column(
            "unit",
            _enum("s", "ms", "mb", "percent", "chars", "clicks", "count", "number", name="unit"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "name ~ '^[a-z][a-z0-9_]{0,39}$'", name=op.f("ck_reference_params_name")
        ),
        sa.CheckConstraint("min_value < max_value", name=op.f("ck_reference_params_bounds")),
        sa.CheckConstraint(
            "value >= min_value AND value <= max_value", name=op.f("ck_reference_params_in_range")
        ),
        sa.ForeignKeyConstraint(
            ["check_id"],
            ["reference_checks.id"],
            name=op.f("fk_reference_params_check_id_reference_checks"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reference_params")),
        sa.UniqueConstraint("check_id", "name", name=op.f("uq_reference_params_check_id_name")),
    )
    op.execute(FROZEN)
    op.execute(
        "CREATE TRIGGER reference_checks_frozen BEFORE UPDATE OR DELETE ON reference_checks"
        " FOR EACH ROW EXECUTE FUNCTION reference_frozen()"
    )
    op.execute(
        "CREATE TRIGGER reference_params_frozen BEFORE UPDATE OR DELETE ON reference_params"
        " FOR EACH ROW EXECUTE FUNCTION reference_frozen()"
    )


def downgrade() -> None:
    """Таблицы — в обратном порядке, с ними уходят триггеры; затем индекс и функция."""
    op.drop_table("reference_params")
    op.drop_table("reference_checks")
    op.drop_index(
        "uq_reference_revisions_active",
        table_name="reference_revisions",
        postgresql_where=sa.text("status = 'active'"),
    )
    op.drop_table("reference_revisions")
    op.execute("DROP FUNCTION reference_frozen()")
