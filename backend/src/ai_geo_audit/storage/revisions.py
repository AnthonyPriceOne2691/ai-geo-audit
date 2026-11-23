"""Цепочка ревизий alembic: номера подряд, без повторов и пропусков (A17).

Сам alembic на повторе номера только предупреждает, в карте оставляет одну ревизию из двух, а
`upgrade head` отказывает словами о разошедшихся ветках; пропуск номера он не замечает вовсе.
Номер — четыре цифры (`0001`): hex-номера сканер секретов принимает за ключи. Файлы читаются
текстом, без alembic: иначе его предупреждение о повторе опередило бы эту проверку.
"""

from __future__ import annotations

import re
from pathlib import Path

from ai_geo_audit.storage.models import NothingToCheck

_REVISION = re.compile(r"(?m)^revision\s*(?::[^=\n]*)?=\s*[\"'](\d{4})[\"']")
_DOWN = re.compile(r"(?m)^down_revision\s*(?::[^=\n]*)?=\s*(?:[\"'](\d{4})[\"']|None)")


def chain_problems(versions: Path) -> list[str]:
    """Нарушения цепочки в каталоге ревизий: номер меньше `0001`, повтор номера с именами файлов,
    пропуск номера, `down_revision` не на предыдущий номер, файл без номера. Ревизий нет — отказ."""
    files: dict[str, list[str]] = {}
    downs: dict[str, str | None] = {}
    problems = []
    for path in sorted(versions.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        found, down = _REVISION.search(source), _DOWN.search(source)
        if found is None or down is None:
            problems.append(f"{path.name}: нет номера ревизии вида 0001 или её down_revision")
            continue
        files.setdefault(found.group(1), []).append(path.name)
        downs[found.group(1)] = down.group(1)
    if not files and not problems:
        raise NothingToCheck(f"ревизий не найдено: просмотрен каталог {versions}")
    for number, names in files.items():
        if int(number) < 1:
            problems.append(f"номер {number}: цепочка начинается с 0001")
        if len(names) > 1:
            problems.append(f"номер {number} у нескольких ревизий: {', '.join(names)}")
    last = max((int(number) for number in files), default=0)
    for n in range(1, last + 1):
        current, previous = f"{n:04d}", f"{n - 1:04d}" if n > 1 else None
        if current not in files:
            problems.append(f"номер {current} пропущен")
        elif downs[current] != previous:
            problems.append(f"у {current} down_revision {downs[current]} вместо {previous}")
    return sorted(problems)
