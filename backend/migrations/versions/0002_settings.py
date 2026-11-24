"""Таблицы ключей сервисов и версий обычных настроек (Ч1.12а, A236).

Ревизия 0002, после 0001. Создана 2026-11-24 13:55:36.524330+00:00 командой
`alembic revision --autogenerate`.

Соглашение: колонка, добавленная задним числом, получает NULL — «неизвестно», а не умолчание,
которое читается как измеренное значение. Номер — следующий по порядку; кто сливает вторым,
перенумеровывает свою ревизию.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Ключи сервисов — строкой на ключ, шифротекстом; настройки — версиями."""
    op.create_table(
        "service_keys",
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("master_id", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.Text(), nullable=False),
        sa.Column("hint", sa.Text(), nullable=True),
        sa.Column(
            "set_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "fingerprint ~ '^[0-9a-f]{12}$'", name=op.f("ck_service_keys_fingerprint")
        ),
        sa.CheckConstraint("master_id ~ '^[0-9a-f]{8}$'", name=op.f("ck_service_keys_master_id")),
        sa.CheckConstraint("name ~ '^[a-z][a-z0-9_]{1,62}$'", name=op.f("ck_service_keys_name")),
        sa.CheckConstraint("char_length(hint) = 4", name=op.f("ck_service_keys_hint")),
        sa.PrimaryKeyConstraint("name", name=op.f("pk_service_keys")),
    )
    op.create_table(
        "setting_versions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("version >= 1", name=op.f("ck_setting_versions_version")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_setting_versions")),
        sa.UniqueConstraint("name", "version", name=op.f("uq_setting_versions_name_version")),
    )


def downgrade() -> None:
    """Обе таблицы — вон, в обратном порядке."""
    op.drop_table("setting_versions")
    op.drop_table("service_keys")
