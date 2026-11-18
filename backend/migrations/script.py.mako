"""${message}

Ревизия ${up_revision}, после ${down_revision}. Создана ${create_date}.

Соглашение: колонка, добавленная задним числом, получает NULL — «неизвестно», а не умолчание,
которое читается как измеренное значение. Номер — следующий по порядку; кто сливает вторым,
перенумеровывает свою ревизию.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
${imports if imports else ""}
revision: str = ${repr(up_revision)}
down_revision: str | None = ${repr(down_revision)}
branch_labels: str | None = ${repr(branch_labels)}
depends_on: str | None = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
