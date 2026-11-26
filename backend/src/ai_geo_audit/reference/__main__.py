"""`python -m ai_geo_audit.reference check <файл|-> [--manifest <путь>]`: проверка файла справочника
без записи (Ч2.1). Код 0 — годен, итог в вывод; 1 — отказ, причины в поток ошибок; 2 — не тот вызов.
Файл и манифест — до базы: битый файл базы не касается (decisions). Журналы и крючки — до работы
(L54, L55); негодную настройку вход ловит вокруг импорта (L30); класс отказа — в `reference.file`:
под `python -m` модуль исполняется как `__main__` (L123). Непредвиденный сбой — строкой со своим
типом, без текста исключения: в нём бывает текст файла (L57, L118)."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from ai_geo_audit.refusal import invalid_settings

try:
    from ai_geo_audit import config
    from ai_geo_audit.logs.setup import guard_process, setup_logging
    from ai_geo_audit.reference import file, manifest
except ValidationError as invalid:
    raise SystemExit(invalid_settings("reference", invalid)) from None

USAGE = "использование: python -m ai_geo_audit.reference check <файл|-> [--manifest <путь>]"


def _args(argv: Sequence[str]) -> tuple[str, str | None] | None:
    """Файл и манифест из аргументов; иной вид — None."""
    match list(argv):
        case ["check", source]:
            return source, None
        case ["check", source, "--manifest", path]:
            return source, path
    return None


def main(argv: Sequence[str]) -> int:
    """`check` — проверка файла; иное — строка использования, код 2."""
    args = _args(argv)
    if args is None:
        sys.stderr.write(USAGE + "\n")
        return 2
    setup_logging()
    guard_process()
    try:
        lines = _check(*args)
    except file.FileRefusedError as refused:
        sys.stderr.write("\n".join(refused.reasons) + "\n")
        return 1
    except Exception as failure:  # silent-ok: сбой — строкой со своим типом, без текста (L57)
        sys.stderr.write(f"сбой проверки: {type(failure).__name__} — справочник не тронут\n")
        return 1
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


def _check(source: str, manifest_path: str | None) -> list[str]:
    """Файл и манифест до базы (A305–A308): в бою — только манифест из образа."""
    if manifest_path is not None and config.app.env == "prod":
        raise file.FileRefusedError(["в бою — только манифест из образа"])
    expected = manifest.load(Path(manifest_path)) if manifest_path else manifest.MANIFEST
    found = file.parse(_read(source))
    why = manifest.mismatches(found, expected)
    if why:
        raise file.FileRefusedError(why)
    rows, day = len(found.rows), found.source_date.isoformat()
    return [f"файл годен: строк {rows}, снимок {day}; с манифестом совпадает"]


def _read(source: str) -> bytes:
    """Файл по пути или со стандартного ввода (`-`): не открылся — причина без пути."""
    if source == "-":
        return file.read(sys.stdin.buffer)
    try:
        with Path(source).open("rb") as stream:
            return file.read(stream)
    except OSError as failure:
        reason = failure.strerror or type(failure).__name__
        raise file.FileRefusedError([f"файл не открылся: {reason}"]) from None


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
