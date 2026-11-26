"""`python -m ai_geo_audit.reference check <файл|-> [--manifest <путь>]`: проверка файла справочника
без записи (Ч2.1). Код 0 — годен, итог в вывод; 1 — отказ, причины в поток ошибок; 2 — не тот вызов.
Порядок: проверка старта; файл и манифест — до базы: битый файл базы не касается (decisions); затем
«было — станет» против активной редакции и знак записи. Журналы и крючки — до работы (L54, L55);
негодную настройку вход ловит вокруг импорта (L30); класс отказа — в `reference.file`: под
`python -m` модуль исполняется как `__main__` (L123). Непредвиденный сбой — строкой со своим
типом, без текста исключения: в нём бывает текст файла (L57, L118)."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from ai_geo_audit.refusal import invalid_settings

try:
    from ai_geo_audit import config
    from ai_geo_audit.config.startup_checks import ConfigError, check_startup
    from ai_geo_audit.logs.setup import guard_process, setup_logging
    from ai_geo_audit.reference import file, manifest, store
    from ai_geo_audit.reference.models import RevisionSource
    from ai_geo_audit.storage import build_engine, session_scope
except ValidationError as invalid:
    raise SystemExit(invalid_settings("reference", invalid)) from None

USAGE = "использование: python -m ai_geo_audit.reference check <файл|-> [--manifest <путь>]"
GONE = (
    "строки справочника не удаляются — верните её в файл; снять проверку с аудитов — убрать её из"
)


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
        check_startup("reference")
        lines = _check(*args)
    except ConfigError as refusal:
        sys.stderr.write(f"{refusal}\n")
        return 1
    except file.FileRefusedError as refused:
        sys.stderr.write("\n".join(refused.reasons) + "\n")
        return 1
    except Exception as failure:  # silent-ok: сбой — строкой со своим типом, без текста (L57)
        sys.stderr.write(f"сбой проверки: {type(failure).__name__} — справочник не тронут\n")
        return 1
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


def _check(source: str, manifest_path: str | None) -> list[str]:
    """Файл и манифест до базы (A305–A308), затем база (A304, A312); в бою — манифест образа."""
    if manifest_path is not None and config.app.env == "prod":
        raise file.FileRefusedError(["в бою — только манифест из образа"])
    expected = manifest.load(Path(manifest_path)) if manifest_path else manifest.MANIFEST
    found = file.parse(_read(source))
    why = manifest.mismatches(found, expected)
    if why:
        raise file.FileRefusedError(why)
    rows, day = len(found.rows), found.source_date.isoformat()
    good = f"файл годен: строк {rows}, снимок {day}; с манифестом совпадает"
    return [good, *asyncio.run(_against(found))]


async def _against(found: file.ReferenceFile) -> list[str]:
    """«Было — станет» против активной редакции, правки, которые запись отменит, и знак записи
    `<sha256>@<номер>`; пропавшая строка — отказ без знака (A312). База не меняется."""
    engine = build_engine("reference")
    try:
        async with session_scope(engine) as session:
            try:
                active = await store.active_revision(session)
            except store.NotLoadedError:
                return [f"новых строк {len(found.rows)}", f"{found.sha256}@0"]
            rows = await store.revision_rows(session, active.id)
            base = await store.last_file_revision(session, active.number)
            edited = active.source is RevisionSource.EDIT and base is not None
            old = await store.revision_rows(session, base.id) if edited and base else rows
    finally:
        await engine.dispose()
    gone = store.missing(rows, found.rows)
    if gone:
        raise file.FileRefusedError([f"строки {code} нет в файле: {GONE} наборов" for code in gone])
    edits = {key for key, _ in store.diff(old, rows)}
    cancel = f"будут отменены правки редакции {active.number}: "
    lines = [cancel + line if key in edits else line for key, line in store.diff(rows, found.rows)]
    return [
        *(lines or [f"изменений нет: файл равен редакции {active.number}"]),
        f"{found.sha256}@{active.number}",
    ]


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
