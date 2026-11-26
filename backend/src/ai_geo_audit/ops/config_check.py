"""`python -m ai_geo_audit.ops.config_check <процесс>`: разбор конфига процесса без старта (A189).

Тот же приговор, что даст старт процесса, — без старта. Негодное значение роняет импорт конфига:
команда ловит `ValidationError` вокруг импорта (L30), текст — `ai_geo_audit.refusal`, все поля
упавшего домена разом (домены строятся по очереди). Недостающее и негодное в бою называет
проверка старта — все имена процесса разом. Сервис не запускается, в базу и Redis не ходит: адреса
только разбираются. Код 0 — годен, «проверено имён: N» — все имена конфига; 1 — не годен; 2 —
строка использования. Во всём выводе нет `Traceback` и введённого значения (L29).

Модуль — в `ops`, вне слоёв, а не в пакете `config`: `-m` импортирует пакеты-родители раньше
модуля, и пакет конфига упал бы раньше `try` (проба 2026-11-26T12:20Z; имя команды — слово
владельца 2026-11-26T12:30:27Z).
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from typing import TYPE_CHECKING

from pydantic import ValidationError

from ai_geo_audit.refusal import invalid_settings

if TYPE_CHECKING:
    from ai_geo_audit.config.startup_checks import Process

#: Процессы проверки старта (`startup_checks.Process`): её модуль грузит конфиг, и до `try` его не
#: импортировать — список свой, равенство держит тест.
PROCESSES: tuple[Process, ...] = ("api", "worker", "migrate", "reference")
USAGE = "использование: python -m ai_geo_audit.ops.config_check api|worker|migrate|reference"


def _process(argv: Sequence[str]) -> Process | None:
    """Процесс из аргументов: ровно один и из списка; иначе None."""
    for process in PROCESSES:
        if list(argv) == [process]:
            return process
    return None


def main(argv: Sequence[str]) -> int:
    """Импорт конфига, затем проверка старта процесса — без старта; отказы словами."""
    process = _process(argv)
    if process is None:
        sys.stderr.write(USAGE + "\n")
        return 2
    try:
        from ai_geo_audit import config
        from ai_geo_audit.config.startup_checks import ConfigError, check_startup
    except ValidationError as invalid:
        sys.stderr.write(invalid_settings(process, invalid) + "\n")
        return 1
    try:
        check_startup(process)
    except ConfigError as refusal:
        sys.stderr.write(f"{refusal}\n")
        return 1
    names = len(config.env_names())
    sys.stdout.write(
        f"Конфиг процесса «{process}» годен, режим {config.app.env}: проверено имён: {names}\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
