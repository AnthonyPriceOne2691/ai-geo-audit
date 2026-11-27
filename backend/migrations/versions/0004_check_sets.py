"""Таблицы наборов — наборы, версии, строки состава, — их триггер и встроенный набор (Ч2.2).

Ревизия 0004, после 0003. Создана 2026-11-27 13:59:23.027908+00:00 командой
`alembic revision --autogenerate`; функция и триггеры неизменности и вставка встроенного набора
дописаны руками: alembic их не видит, их держит A320 после подъёма и отката.

Соглашение: колонка, добавленная задним числом, получает NULL — «неизвестно», а не умолчание,
которое читается как измеренное значение. Номер — следующий по порядку; кто сливает вторым,
перенумеровывает свою ревизию.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | None = None
depends_on: str | None = None

#: Одна функция на три таблицы (decisions, пробы 1–1г шага 0). Набор: вид и имя неизменны. Версия:
#: черновик правится и удаляется; у опубликованной меняются только статус и `archived_at`, удалить
#: её нельзя. Строка состава: вставка, правка и удаление — только у черновика, на правке сверяются
#: прежняя версия и новая; статус версии строка читает под `FOR SHARE` — публикация черновика ждёт
#: незафиксированную правку его строк (находка второй проверяющей 2026-11-27T14:27Z, decisions).
#: Отказ — имя ограничения `check_set_frozen` в ответе базы, как у `reference_frozen`; нет версии
#: строки — отказ ключа, а не этот.
FROZEN = """
CREATE FUNCTION check_set_frozen() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_TABLE_NAME = 'check_sets' THEN
        IF NEW.kind = OLD.kind AND NEW.name = OLD.name THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'вид и имя набора не меняются — нужное имя — копией'
            USING ERRCODE = 'restrict_violation', CONSTRAINT = 'check_set_frozen';
    ELSIF TG_TABLE_NAME = 'check_set_versions' THEN
        IF OLD.status = 'draft' THEN
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END IF;
        IF TG_OP = 'UPDATE' AND (to_jsonb(NEW) - 'status' - 'archived_at')
                = (to_jsonb(OLD) - 'status' - 'archived_at') THEN
            RETURN NEW;
        END IF;
    ELSE
        IF TG_OP <> 'INSERT' AND (SELECT status FROM check_set_versions WHERE id = OLD.version_id
                FOR SHARE) <> 'draft' THEN
            RAISE EXCEPTION 'опубликованная версия набора не меняется — правка идёт черновиком'
                USING ERRCODE = 'restrict_violation', CONSTRAINT = 'check_set_frozen';
        END IF;
        IF TG_OP <> 'DELETE' AND (SELECT status FROM check_set_versions WHERE id = NEW.version_id
                FOR SHARE) <> 'draft' THEN
            RAISE EXCEPTION 'опубликованная версия набора не меняется — правка идёт черновиком'
                USING ERRCODE = 'restrict_violation', CONSTRAINT = 'check_set_frozen';
        END IF;
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
    END IF;
    RAISE EXCEPTION 'опубликованная версия набора не меняется — правка идёт черновиком'
        USING ERRCODE = 'restrict_violation', CONSTRAINT = 'check_set_frozen';
END
$$
"""
TRIGGERS = {
    "check_sets": "UPDATE",
    "check_set_versions": "UPDATE OR DELETE",
    "check_set_items": "INSERT OR UPDATE OR DELETE",
}
#: Встроенный набор: вид `all_checks`, версия 1 активна, строк состава нет.
ALL_CHECKS = (
    "INSERT INTO check_sets (name, kind) VALUES ('Все проверки', 'all_checks')",
    "INSERT INTO check_set_versions (set_id, number, status, published_at)"
    " SELECT id, 1, 'active', now() FROM check_sets WHERE kind = 'all_checks'",
)


def _enum(*values: str, name: str) -> sa.Enum:
    """Перечисление, как его даёт `storage.models.enum_column`: строка 32 с проверкой."""
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True, length=32)


def upgrade() -> None:
    """Три таблицы, частичные индексы, функция и три триггера неизменности, встроенный набор."""
    op.create_table(
        "check_sets",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("kind", _enum("custom", "all_checks", name="kind"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(description) <= 1000", name=op.f("ck_check_sets_description")
        ),
        sa.CheckConstraint(
            "char_length(name) >= 1 AND char_length(name) <= 200", name=op.f("ck_check_sets_name")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_check_sets")),
        sa.UniqueConstraint("name", name=op.f("uq_check_sets_name")),
    )
    op.create_index(
        "uq_check_sets_all_checks",
        "check_sets",
        ["kind"],
        unique=True,
        postgresql_where=sa.text("kind = 'all_checks'"),
    )
    op.create_table(
        "check_set_versions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("set_id", sa.BigInteger(), nullable=False),
        sa.Column("number", sa.BigInteger(), nullable=False),
        sa.Column("status", _enum("draft", "active", "archived", name="status"), nullable=False),
        sa.Column("saves", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "platform_models",
            _enum("base", "lite", name="platform_models"),
            server_default="base",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(status = 'draft') = (published_at IS NULL)"
            " AND (status = 'archived') = (archived_at IS NOT NULL)",
            name=op.f("ck_check_set_versions_dates"),
        ),
        sa.CheckConstraint("number >= 1", name=op.f("ck_check_set_versions_number")),
        sa.CheckConstraint("saves >= 0", name=op.f("ck_check_set_versions_saves")),
        sa.ForeignKeyConstraint(
            ["set_id"],
            ["check_sets.id"],
            name=op.f("fk_check_set_versions_set_id_check_sets"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_check_set_versions")),
        sa.UniqueConstraint("set_id", "number", name=op.f("uq_check_set_versions_set_id_number")),
    )
    for status in ("active", "draft"):
        op.create_index(
            f"uq_check_set_versions_{status}",
            "check_set_versions",
            ["set_id"],
            unique=True,
            postgresql_where=sa.text(f"status = '{status}'"),
        )
    op.create_table(
        "check_set_items",
        sa.Column("version_id", sa.BigInteger(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("kind", _enum("check", "metric", "answer", name="kind"), nullable=False),
        sa.CheckConstraint(
            r"(kind <> 'check' OR code ~ '^[0-9]{1,2}(\.[0-9]{1,2})?$')"
            " AND (kind <> 'metric' OR (code ~ '^[a-z][a-z_]{2,39}$' AND code !~ '^answer_'))"
            " AND (kind <> 'answer' OR code ~ '^answer_[a-z_]{2,32}$')",
            name=op.f("ck_check_set_items_code"),
        ),
        sa.ForeignKeyConstraint(
            ["version_id"],
            ["check_set_versions.id"],
            name=op.f("fk_check_set_items_version_id_check_set_versions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("version_id", "code", name=op.f("pk_check_set_items")),
    )
    op.execute(FROZEN)
    for table, events in TRIGGERS.items():
        op.execute(
            f"CREATE TRIGGER {table}_frozen BEFORE {events} ON {table}"
            " FOR EACH ROW EXECUTE FUNCTION check_set_frozen()"
        )
    for statement in ALL_CHECKS:
        op.execute(statement)


def downgrade() -> None:
    """Таблицы — в обратном порядке, с ними уходят индексы, триггеры и встроенный набор; затем
    функция."""
    for table in reversed(TRIGGERS):
        op.drop_table(table)
    op.execute("DROP FUNCTION check_set_frozen()")
