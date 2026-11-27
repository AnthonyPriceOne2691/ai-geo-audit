"""`python -m ai_geo_audit.reference`: проверка файла справочника без записи (`check`, Ч2.1),
запись файла новой редакцией (`load`) и активная редакция (`status`, Ч2.1б). Код 0 — годен или
записано, итог в вывод; 1 — отказ, причины в поток ошибок; 2 — не тот вызов.
Порядок: проверка старта; знак и причина `load`, файл и манифест — до базы: негодный вызов и битый
файл базы не касаются (decisions); затем база. Журналы и крючки — до работы (L54, L55); негодную
настройку вход ловит вокруг импорта (L30); класс отказа — в `reference.file`: под `python -m`
модуль исполняется как `__main__` (L123). Непредвиденный сбой — строкой со своим типом, без текста
исключения: в нём бывает текст файла (L57, L118)."""

from __future__ import annotations

import asyncio
import re
import shlex
import sys
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path

from pydantic import ValidationError

from ai_geo_audit.refusal import invalid_settings

try:
    from ai_geo_audit import config
    from ai_geo_audit.config.startup_checks import ConfigError, check_startup
    from ai_geo_audit.logs.setup import guard_process, setup_logging
    from ai_geo_audit.reference import file, manifest, store
    from ai_geo_audit.reference.file import RowIn
    from ai_geo_audit.reference.models import ReferenceRevision, RevisionSource
    from ai_geo_audit.storage import build_engine, session_scope
except ValidationError as invalid:
    raise SystemExit(invalid_settings("reference", invalid)) from None

_ME = "python -m ai_geo_audit.reference"
USAGE = (
    f"использование: {_ME} check <файл|-> [--manifest <путь>]\n"
    f"  или: {_ME} load <файл|-> --expect <знак> --reason <текст> [--replace-edits]"
    " [--manifest <путь>]\n"
    f"  или: {_ME} status"
)
#: Готовая запись в выводе `check` перед знаком (A311): `--replace-edits` человек пишет сам.
LOAD_LINE = 'запись: {} load {} --expect {} --reason "<чьё слово и когда>"'
REASON_MAX = 500
_SIGN = re.compile(r"([0-9a-f]{64})@(0|[1-9][0-9]*)")
_VALUED = ("--expect", "--reason", "--manifest")
_FAILURE = {"check": "сбой проверки", "load": "сбой записи", "status": "сбой чтения"}


@dataclass(frozen=True)
class Call:
    """Разобранный вызов: команда, файл и манифест; у `load` — знак, причина, отмена правки."""

    verb: str
    source: str = ""
    manifest: str | None = None
    expect: str | None = None
    reason: str | None = None
    replace_edits: bool = False


def _args(argv: Sequence[str]) -> Call | None:
    """Вызов из аргументов; иной вид — None."""
    match list(argv):
        case ["status"]:
            return Call("status")
        case ["check", source]:
            return Call("check", source)
        case ["check", source, "--manifest", path]:
            return Call("check", source, path)
        case ["load", source, *rest] if not source.startswith("--"):
            return _load_args(source, rest)
    return None


def _load_args(source: str, rest: list[str]) -> Call | None:
    """Флаги `load` в любом порядке, каждый не больше раза; флаг без значения или чужой — None."""
    found: dict[str, str] = {}
    replace = False
    items = iter(rest)
    for flag in items:
        if flag == "--replace-edits" and not replace:
            replace = True
        elif flag in _VALUED and flag not in found and (value := next(items, None)) is not None:
            found[flag] = value
        else:
            return None
    expect, reason = found.get("--expect"), found.get("--reason")
    return Call("load", source, found.get("--manifest"), expect, reason, replace)


def main(argv: Sequence[str]) -> int:
    """`check`, `load` или `status`; иное — строка использования, код 2."""
    call = _args(argv)
    if call is None:
        sys.stderr.write(USAGE + "\n")
        return 2
    setup_logging()
    guard_process()
    try:
        check_startup("reference")
        lines = _run(call)
    except ConfigError as refusal:
        sys.stderr.write(f"{refusal}\n")
        return 1
    except file.FileRefusedError as refused:
        sys.stderr.write("\n".join(refused.reasons) + "\n")
        return 1
    except (store.NotLoadedError, store.WriteFailedError, store.CommitFailedError) as failure:
        sys.stderr.write(f"{failure}\n")
        return 1
    except Exception as failure:  # silent-ok: сбой — строкой со своим типом, без текста (L57)
        kind = type(failure).__name__
        sys.stderr.write(f"{_FAILURE[call.verb]}: {kind} — справочник не тронут\n")
        return 1
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


def _run(call: Call) -> list[str]:
    if call.verb == "status":
        return asyncio.run(_status())
    if call.verb == "load":
        expect, reason = _signed(call)
        found = _file(call)
        return [_written(asyncio.run(_load(found, expect, reason, call.replace_edits)))]
    found = _file(call)
    good = (
        f"файл годен: строк {len(found.rows)}, снимок {found.source_date}; с манифестом совпадает"
    )
    return [good, *asyncio.run(_against(found, call.source))]


def _signed(call: Call) -> tuple[tuple[str, int], str]:
    """Знак и причина `load` до файла и базы (A309), все причины разом: знак — вида
    `<sha256>@<номер>`; причина — не пусто, до 500 знаков, одной строкой без управляющих знаков
    (Cc и разделители строк, которые делит `splitlines`)."""
    sign = _SIGN.fullmatch(call.expect or "")
    why = [] if sign else ["нужен знак из проверки: --expect <sha256>@<номер>"]
    reason = call.reason or ""
    if not reason.strip():
        why.append("нужна причина — чьё слово и когда")
    else:
        if len(reason) > REASON_MAX:
            why.append(f"причина — до {REASON_MAX} знаков")
        if reason.splitlines() != [reason] or any(unicodedata.category(c) == "Cc" for c in reason):
            why.append("причина — одной строкой, без управляющих знаков")
    if sign is None or why:
        raise file.FileRefusedError(why)
    return (sign[1], int(sign[2])), reason


def _file(call: Call) -> file.ReferenceFile:
    """Файл и манифест до базы (A305–A308); в бою — только манифест образа."""
    if call.manifest is not None and config.app.env == "prod":
        raise file.FileRefusedError(["в бою — только манифест из образа"])
    expected = manifest.load(Path(call.manifest)) if call.manifest else manifest.MANIFEST
    found = file.parse(_read(call.source))
    why = manifest.mismatches(found, expected)
    if why:
        raise file.FileRefusedError(why)
    return found


async def _against(found: file.ReferenceFile, source: str) -> list[str]:
    """«Было — станет» против активной редакции, правки, которые запись отменит, готовая запись и
    знак `<sha256>@<номер>`; пропавшая строка — отказ без знака (A312). База не меняется."""
    engine = build_engine("reference")
    try:
        async with session_scope(engine) as session:
            try:
                active = await store.active_revision(session)
            except store.NotLoadedError:
                return _tail([f"новых строк {len(found.rows)}"], found, source, 0)
            rows = await store.revision_rows(session, active.id)
            base = await store.last_file_revision(session, active.number)
            edited = active.source is RevisionSource.EDIT and base is not None
            old = await store.revision_rows(session, base.id) if edited and base else rows
    finally:
        await engine.dispose()
    gone = store.missing_reasons(rows, found.rows)
    if gone:
        raise file.FileRefusedError(gone)
    edits = {key for key, _ in store.diff(old, rows)}
    cancel = f"будут отменены правки редакции {active.number}: "
    lines = [cancel + line if key in edits else line for key, line in store.diff(rows, found.rows)]
    same = [f"изменений нет: файл равен редакции {active.number}"]
    return _tail(lines or same, found, source, active.number)


def _tail(lines: list[str], found: file.ReferenceFile, source: str, number: int) -> list[str]:
    """Строки `check` и в конце готовая запись и знак — последней строкой (A304, A311)."""
    sign = f"{found.sha256}@{number}"
    return [*lines, LOAD_LINE.format(_ME, shlex.quote(source), sign), sign]


async def _load(
    found: file.ReferenceFile, expect: tuple[str, int], reason: str, replace: bool
) -> store.Written:
    engine = build_engine("reference")
    try:
        return await store.load(engine, found, expect=expect, reason=reason, replace_edits=replace)
    finally:
        await engine.dispose()


def _written(written: store.Written) -> str:
    if written.already:
        return f"уже загружено: редакция {written.number}"
    return f"редакция {written.number} активна: строк {written.rows}, порогов {written.params}"


async def _status() -> list[str]:
    """Активная редакция (A311); нет её — отказ «справочник не загружен»."""
    engine = build_engine("reference")
    try:
        async with session_scope(engine) as session:
            active = await store.active_revision(session)
            rows = await store.revision_rows(session, active.id)
            based = active.based_on_id
            base = await session.get(ReferenceRevision, based) if based else None
    finally:
        await engine.dispose()
    return [_describe(active, base, rows)]


def _describe(active: ReferenceRevision, base: ReferenceRevision | None, rows: list[RowIn]) -> str:
    """Строка `status`: номер, источник, строк и порогов, у файла — sha256 и дата снимка,
    причина, время записи — UTC с Z."""
    counts = f"строк {len(rows)}, порогов {sum(len(row.params) for row in rows)}"
    if active.source is RevisionSource.FILE:
        where = f"файл, {counts}, sha256 {active.file_sha256}, снимок {active.source_date}"
    else:
        where = f"правка на основе {base.number if base else '—'}, {counts}"
    why = f", причина «{active.reason}»" if active.reason else ""
    at = active.created_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"редакция {active.number} активна: {where}{why}, записана {at}"


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
