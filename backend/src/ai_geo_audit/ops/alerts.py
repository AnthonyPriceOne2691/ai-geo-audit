"""`python -m ai_geo_audit.ops.alerts check`: тревога ПРОВЕРКА в исходящие — проверка канала (A242).

Код 0 — поставлено; 1 — нет: причина — строкой журнала, или негодная настройка — словами. Модуль
вне слоёв (В5); команду `pair` сюда положит Ч1.14а2. Журналы и крючки процесса — до работы: все
пути текста в поток ошибок идут через вычистку (L54, L55). Негодная настройка роняет импорт
конфига — вход ловит её вокруг импорта (L30); текст отказа — `ai_geo_audit.refusal`, а не класс
этого модуля: под `python -m` модуль исполняется как `__main__` (L123).
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from pydantic import ValidationError

from ai_geo_audit.refusal import invalid_settings

try:
    from ai_geo_audit import alerts
    from ai_geo_audit.logs.setup import guard_process, setup_logging
except ValidationError as invalid:
    raise SystemExit(invalid_settings("alerts", invalid)) from None

USAGE = "использование: python -m ai_geo_audit.ops.alerts check"


def main(argv: Sequence[str]) -> int:
    """`check` — тревога ПРОВЕРКА в исходящие; иное — строка использования, код 2."""
    if list(argv) != ["check"]:
        sys.stderr.write(USAGE + "\n")
        return 2
    setup_logging()
    guard_process()
    queued = alerts.queue_alert(
        "ПРОВЕРКА",
        "канал тревог",
        ["Проверочная тревога: исходящие сервиса принимают записи."],
        "ничего — это проверка",
    )
    if queued:
        sys.stdout.write("поставлено в исходящие\n")
    return 0 if queued else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
