"""Запись справочника файлом (A309, A311, A313, A314): `load` и `status` — в процессе теста через
`main` и подпроцессом, как в бою; гонки — записи на своих соединениях, замок держит соединение
теста, ожидание видно в `pg_locks`. Редакции до записи ставит помощник сырых вставок; после теста
таблицы справочника пусты. Знаки вне ASCII — через `chr` (L128)."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import shlex
import subprocess
import sys
from collections.abc import AsyncIterator, Callable, Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
import dbtools
import pytest
from reference_sample import (
    SAMPLE,
    SAMPLE2,
    SNAPSHOT,
    document,
    dump,
    empty,
    files,
    insert_revision,
    manifest_of,
    revisions,
)
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from test_alerts_commands import process_env
from test_reference_check import COMMAND, DEFAULT, EDITED, F2, GONE, NL, STARTUP, F, sign, with92
from test_reference_file import MP, Cap, in_prod, quiet

from ai_geo_audit import config
from ai_geo_audit.reference import __main__ as command
from ai_geo_audit.reference import file, store
from ai_geo_audit.reference.file import UNITS
from ai_geo_audit.storage import locks

Cli = Callable[..., tuple[int, str, str]]
#: Отказ `load` без знака или со знаком не того вида (A309).
EXPECT = "нужен знак из проверки: --expect <sha256>@<номер>"
REASON = "нужна причина — чьё слово и когда"
ONE_LINE = "причина — одной строкой, без управляющих знаков"
CHANGED = "справочник изменился после проверки: была {}, активна {} — повторите check"
WROTE = "редакция {} активна: строк {}, порогов {}" + NL
EDITS = (
    "после файла справочник правили: редакция 2 — добавьте --replace-edits, если правку нужно"
    " отменить"
)
EDIT_OVER_1 = (
    {"rows": SAMPLE, "number": 1, "status": "archived"},
    {"rows": EDITED, "number": 2, "source": "edit"},
)
HEADS = (
    "SELECT r.number, r.status, r.source, b.number, r.file_sha256, r.source_date, r.reason,"
    " r.archived_at IS NOT NULL, r.file_rows FROM reference_revisions r"
    " LEFT JOIN reference_revisions b ON b.id = r.based_on_id ORDER BY r.number"
)
ROWS = (
    "SELECT c.code, p.name, p.value, p.min_value, p.max_value FROM reference_checks c"
    " JOIN reference_revisions r ON r.id = c.revision_id"
    " LEFT JOIN reference_params p ON p.check_id = c.id WHERE r.number = $1 ORDER BY c.position"
)
WRITTEN_AT = (
    "SELECT to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"')"
    " FROM reference_revisions WHERE number = $1"
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def signed(data: bytes, number: int, reason: str = "проба") -> tuple[str, ...]:
    """Знак `data` при активной `number` и причина — аргументы `load`."""
    return "--expect", sign(data, number), "--reason", reason


def expected(rows: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    """Строки редакции по месту: код и порог с пределами единицы; без порога — пустые поля."""
    found: list[tuple[Any, ...]] = []
    for item in rows:
        for p in item["params"] or [None]:
            found.append(
                (item["code"], None, None, None, None)
                if p is None
                else (item["code"], p["name"], p["value"], *UNITS[p["unit"]])
            )
    return found


def head(number: int, status: str = "active", base: int | None = None) -> tuple[Any, ...]:
    """Заголовок редакции из Ф с причиной «проба»; строк файла — 6."""
    return (number, status, "file", base, sha(F), SNAPSHOT, "проба", status == "archived", 6)


async def _state(numbers: tuple[int, ...], when: int | None) -> tuple[Any, ...]:
    conn = await asyncpg.connect(dbtools._raw(dbtools.own_url()))
    try:
        heads = [tuple(r) for r in await conn.fetch(HEADS)]
        rows = [[tuple(r) for r in await conn.fetch(ROWS, n)] for n in numbers]
        written = await conn.fetchval(WRITTEN_AT, when) if when else None
        return heads, rows, written
    finally:
        await conn.close()


def state(*numbers: int, when: int | None = None) -> tuple[Any, ...]:
    """Заголовки всех редакций, строки редакций `numbers` и время записи редакции `when`."""
    return asyncio.run(_state(numbers, when))


@pytest.fixture
def cli(tmp_path: Path, monkeypatch: MP, capsys: Cap) -> Iterator[Cli]:
    """`check` или `load` в процессе теста по байтам файла и манифесту к ним: код, вывод (путь
    файла — `<файл>`), поток ошибок; после теста таблицы справочника пусты."""
    quiet(monkeypatch)

    def run(verb: str, data: bytes, *args: str, rows: int | None = None) -> tuple[int, str, str]:
        count = rows or len(json.loads(data)["rows"])
        path, spec = files(tmp_path, data, manifest_of(data, count))
        code = command.main([verb, path, *args, "--manifest", spec])
        out, err = capsys.readouterr()
        return code, out.replace(path, "<файл>"), err

    yield run
    empty()


def status(capsys: Cap) -> tuple[int, str, str]:
    return command.main(["status"]), *capsys.readouterr()


def in_process(args: list[str], data: bytes, **env: str) -> tuple[int, str, str]:
    """Вход подпроцессом `python -m` с байтами на стандартном вводе, как `compose run -T`."""
    done = subprocess.run(
        [sys.executable, "-m", "ai_geo_audit.reference", *args],
        input=data,
        env=process_env(**env),
        capture_output=True,
        timeout=60,
        check=False,
    )
    return done.returncode, done.stdout.decode(), done.stderr.decode()


def test_a311_load_writes_revision_1_and_status_names_it(cli: Cli, capsys: Cap) -> None:
    """A311: пустая база; `load Ф --expect Т(0) --reason проба` — код 0, редакция 1 активна:
    файл, sha256, дата и число строк Ф, причина, пороги с пределами единицы; `status` — она
    целиком, время записи — UTC с Z."""
    assert cli("load", F, *signed(F, 0)) == (0, WROTE.format(1, 6, 1), "")
    heads, rows, written = state(1, when=1)
    assert (heads, rows) == ([head(1)], [expected(SAMPLE)])
    line = f"файл, строк 6, порогов 1, sha256 {sha(F)}, снимок 2026-11-18, причина «проба»"
    assert status(capsys) == (0, f"редакция 1 активна: {line}, записана {written}" + NL, "")


@pytest.mark.usefixtures("cli")
def test_a311_load_from_standard_input_is_the_battle_path(tmp_path: Path) -> None:
    """A311: пустая база; Ф на стандартном вводе — `load - --expect Т(0) --reason проба`
    подпроцессом, путь боя: тот же итог, что у `load Ф`. Ввод больше предела — отказ, как у
    `check -` (A306 Ч2.1), база не тронута."""
    _, spec = files(tmp_path, F, manifest_of(F, 6))
    args = ["load", "-", *signed(F, 0), "--manifest", spec]
    big = F + b" " * (file.MAX_BYTES + 1 - len(F))
    too_big = (1, "", "файл больше 1 МБ — дальше не читается" + NL)
    assert in_process(args, big, DATABASE_URL=dbtools.own_url()) == too_big
    assert revisions() == []
    assert in_process(args, F, DATABASE_URL=dbtools.own_url()) == (0, WROTE.format(1, 6, 1), "")
    assert state(1)[:2] == ([head(1)], [expected(SAMPLE)])


def test_a311_the_same_content_is_already_loaded_with_any_sign(cli: Cli) -> None:
    """A311: та же команда повтором, затем Ф с записью `3.250` — с Т(1) и с Т(0): «уже
    загружено: редакция 1» у всех трёх, редакция одна."""
    same = F.replace(b"3.25", b"3.250")
    assert cli("load", F, *signed(F, 0))[0] == 0
    found = [cli("load", data, *signed(data, n)) for data, n in [(F, 0), (same, 1), (same, 0)]]
    assert found == [(0, "уже загружено: редакция 1" + NL, "")] * 3
    assert revisions() == [(1, "active")]


def test_a311_swapped_rows_are_a_new_revision_in_file_order(cli: Cli) -> None:
    """A311: активна 1 из Ф; Ф с переставленными `91` и `92` — `check` называет порядок, `load`
    с его знаком — не «уже загружено», а редакция 2 из файла на основе 1; строки 2 — в порядке
    файла, строки 1 — в прежнем."""
    revisions({"rows": SAMPLE, "number": 1})
    rows = [SAMPLE[1], SAMPLE[0], *SAMPLE[2:]]
    data = dump(document(rows))
    assert cli("check", data)[1].split(NL)[1] == "порядок: строк на других местах 2"
    assert cli("load", data, *signed(data, 1)) == (0, WROTE.format(2, 6, 1), "")
    heads, found, _ = state(1, 2)
    assert [h[:4] for h in heads] == [(1, "archived", "file", None), (2, "active", "file", 1)]
    assert found == [expected(SAMPLE), expected(rows)]


def test_a311_check_names_the_load_and_the_load_archives_revision_1(cli: Cli) -> None:
    """A311: активна 1 из Ф; `check` Ф2 — перед знаком строка готовой команды, последняя — Т(1);
    `load Ф2 --expect Т(1) --reason «проба 2»` — редакция 2 из файла на основе 1, строк файла 7,
    редакция 1 в архиве с `archived_at`, её строки и пороги прежние."""
    revisions({"rows": SAMPLE, "number": 1})
    before = state(1)[1]
    code, out, err = cli("check", F2)
    assert (code, out.split(NL)[-3:], err) == (
        0,
        [COMMAND.format(sign(F2, 1)), sign(F2, 1), ""],
        "",
    )
    assert cli("load", F2, *signed(F2, 1, "проба 2")) == (0, WROTE.format(2, 7, 1), "")
    heads, after, _ = state(1)
    assert [h[:4] + h[6:] for h in heads] == [
        (1, "archived", "file", None, "проба 1", True, 6),
        (2, "active", "file", 1, "проба 2", False, 7),
    ]
    assert after == before


@pytest.mark.usefixtures("cli")
def test_a311_the_ready_line_keeps_a_path_with_a_space_and_a_quote(
    tmp_path: Path, capsys: Cap
) -> None:
    """A311: путь файла с пробелом и `'` — в готовой строке записи он в кавычках оболочки:
    `shlex.split` строки отдаёт ровно этот путь (план, проба 6)."""
    folder = tmp_path / "файлы ведущей 'v2'"
    folder.mkdir()
    _, spec = files(folder, F, manifest_of(F, 6))
    path = str(folder / "reference.json")
    assert command.main(["check", path, "--manifest", spec]) == 0
    line = capsys.readouterr().out.split(NL)[-3].removeprefix("запись: ")
    load = ["python", "-m", "ai_geo_audit.reference", "load", path]
    assert shlex.split(line) == [*load, "--expect", sign(F, 0), "--reason", "<чьё слово и когда>"]


def test_a311_a_load_over_an_edit_needs_replace_edits(cli: Cli) -> None:
    """A311: активна 2 — правка поверх 1 из Ф (у `92` 3.5): `load Ф --expect Т(2)` без флага —
    отказ с подсказкой, база не тронута; с `--replace-edits` — редакция 3 из файла на основе 2,
    у `92` 3.25, редакция 2 в архиве."""
    revisions(*EDIT_OVER_1)
    assert cli("load", F, *signed(F, 2)) == (1, "", EDITS + NL)
    assert revisions() == [(1, "archived"), (2, "active")]
    assert cli("load", F, *signed(F, 2), "--replace-edits") == (0, WROTE.format(3, 6, 1), "")
    heads, rows, _ = state(3)
    assert [h[:4] for h in heads] == [
        (1, "archived", "file", None),
        (2, "archived", "edit", 1),
        (3, "active", "file", 2),
    ]
    assert rows == [expected(SAMPLE)]


@pytest.mark.usefixtures("cli")
def test_a311_status_names_an_edit_and_refuses_an_empty_reference(capsys: Cap) -> None:
    """A311: пустой справочник — `status` отказывает словами, код 1; активна правка — источник
    «правка на основе 1», без sha256 и даты файла."""
    assert status(capsys) == (1, "", store.NOT_LOADED + NL)
    revisions(*EDIT_OVER_1)
    line = f"правка на основе 1, строк 6, порогов 1, записана {state(when=2)[2]}"
    assert status(capsys) == (0, f"редакция 2 активна: {line}" + NL, "")


@pytest.mark.parametrize(
    ("args", "seeded", "err"),
    [
        (["--reason", "проба"], False, EXPECT),
        (["--expect", "abc@0", "--reason", "проба"], False, EXPECT),
        (["--expect", sha(F) + "@", "--reason", "проба"], False, EXPECT),
        (signed(F2, 0), False, "файл изменился после проверки — повторите check"),
        (signed(F, 0), True, CHANGED.format(0, 1)),
        (["--expect", sign(F, 0)], False, REASON),
        (signed(F, 0, "   "), False, REASON),
        (signed(F, 0, "п" * 501), False, "причина — до 500 знаков"),
        (signed(F, 0, f"слово{chr(0)}"), False, ONE_LINE),
    ],
    ids=[
        "no-sign",
        "not-a-sign",
        "no-number",
        "other-file",
        "other-revision",
        "no-reason",
        "blank-reason",
        "reason-501",
        "reason-nul",
    ],
)
def test_a309_the_sign_and_the_reason_are_checked_before_writing(
    cli: Cli, args: list[str], seeded: bool, err: str
) -> None:
    """A309: без знака, не того вида, знак другого файла, знак прежней редакции, без причины, из
    пробелов, 501 знак, с NUL (аргумент процесса NUL не несёт — только в процессе теста) —
    отказ словами, код 1, база не тронута."""
    before = revisions({"rows": SAMPLE2, "number": 1}) if seeded else revisions()
    assert cli("load", F, *args) == (1, "", err + NL)
    assert revisions() == before


def test_a309_a_reason_of_500_characters_is_accepted(cli: Cli) -> None:
    """A309, соседний годный случай: причина ровно 500 знаков — запись."""
    assert cli("load", F, *signed(F, 0, "п" * 500)) == (0, WROTE.format(1, 6, 1), "")


@pytest.mark.usefixtures("cli")
def test_a309_without_a_sign_the_standard_input_is_not_read(monkeypatch: MP, capsys: Cap) -> None:
    """A309: `load -` без знака — «нужен знак…», код 1; стандартный ввод не читается: знак и
    причина сверяются до файла (decisions, порядок `load`)."""

    class Unread:
        @property
        def buffer(self) -> Any:
            raise AssertionError("ввод прочитан до знака")

    monkeypatch.setattr(sys, "stdin", Unread())
    assert command.main(["load", "-", "--reason", "проба"]) == 1
    assert capsys.readouterr() == ("", EXPECT + NL)


@pytest.mark.parametrize(
    "mark", [chr(10), chr(27), chr(0x2028), chr(0x2029)], ids=["newline", "esc", "u2028", "u2029"]
)
def test_a309_a_reason_of_several_lines_is_refused_in_a_process(tmp_path: Path, mark: str) -> None:
    """A309: причина с переводом строки, `ESC`, U+2028 или U+2029 — подпроцессом, как в бою:
    «одной строкой, без управляющих знаков», до базы и файла."""
    path, spec = files(tmp_path, F, manifest_of(F, 6))
    args = ["load", path, *signed(F, 0, f"слово{mark}владельца"), "--manifest", spec]
    assert in_process(args, b"") == (1, "", ONE_LINE + NL)


def test_a309_a_spoilt_file_or_manifest_is_refused_before_the_database(
    cli: Cli, tmp_path: Path, monkeypatch: MP, capsys: Cap
) -> None:
    """A309: байты не UTF-8; манифест — строк 7; бой и `--manifest`; бой и пустой манифест образа
    — причины `check` (A305, A308 Ч2.1), до соединения: адрес базы — на закрытый порт."""
    dead = make_url(dbtools.own_url()).set(port=1).render_as_string(hide_password=False)
    monkeypatch.setattr(config, "storage", config.StorageSettings(database_url=dead))
    spoilt = chr(255).encode("latin-1") + F
    assert cli("load", spoilt, *signed(F, 0), rows=6) == (1, "", "файл не в UTF-8" + NL)
    rows = "файл не совпадает с манифестом: строк 6, в манифесте 7"
    assert cli("load", F, *signed(F, 0), rows=7) == (1, "", rows + NL)
    in_prod(monkeypatch)
    assert cli("load", F, *signed(F, 0)) == (1, "", "в бою — только манифест из образа" + NL)
    path, _ = files(tmp_path, F, {})
    assert command.main(["load", path, *signed(F, 0)]) == 1
    assert capsys.readouterr() == ("", "манифест не заполнен — файла ведущей в нём ещё нет" + NL)


@pytest.mark.parametrize(
    "args", [["load", "-", "--expect", "x", "--reason", "y"], ["status"]], ids=["load", "status"]
)
def test_a309_in_prod_load_and_status_do_not_start_without_the_database_url(
    args: list[str],
) -> None:
    """A309: `APP_ENV=prod` без `DATABASE_URL` — `load` и `status` подпроцессом: отказ проверки
    старта с именем переменной, целиком, как у `check` (A310 Ч2.1); `Traceback` нет."""
    assert in_process(args, b"", APP_ENV="prod") == (1, "", f"{STARTUP}{NL}{DEFAULT}{NL}")


def test_a309_a_row_missing_from_the_file_is_refused_like_check(cli: Cli) -> None:
    """A309: активна редакция из Ф2; в Ф нет `95` — та же строка, что у `check` (A312 Ч2.1)."""
    before = revisions({"rows": SAMPLE2, "number": 1})
    gone = f"{GONE} аудитов — убрать её из наборов"
    assert cli("load", F, *signed(F, 1)) == (1, "", gone + NL)
    assert revisions() == before


def test_a313_a_failure_before_commit_leaves_the_reference_untouched(
    cli: Cli, monkeypatch: MP
) -> None:
    """A313: активна 1 из Ф; `load Ф2` — вставка падает на четвёртой строке из семи (подмена):
    «сбой записи: <тип> — справочник не тронут», текста исключения нет; строк редакции 2 нет;
    следующий `load` того же файла — редакция 2."""
    revisions({"rows": SAMPLE, "number": 1})
    real = store.insert_row
    calls: list[int] = []

    async def fourth_fails(*args: Any) -> int:
        calls.append(len(calls) + 1)
        if len(calls) == 4:
            raise RuntimeError("подмена: норма 93 из файла")
        return await real(*args)

    failed = (1, "", "сбой записи: RuntimeError — справочник не тронут" + NL)
    with monkeypatch.context() as patch:
        patch.setattr(store, "insert_row", fourth_fails)
        assert cli("load", F2, *signed(F2, 1)) == failed
    heads, rows, _ = state(2)
    assert ([h[:2] for h in heads], rows, calls) == ([(1, "active")], [[]], [1, 2, 3, 4])
    assert cli("load", F2, *signed(F2, 1)) == (0, WROTE.format(2, 7, 1), "")


def test_a313_a_failure_at_commit_is_not_called_untouched(
    cli: Cli, monkeypatch: MP, capsys: Cap
) -> None:
    """A313: сбой — на фиксации: подмена бросает после настоящей фиксации — «могла пройти,
    проверьте status»; `status` — редакция 2."""
    revisions({"rows": SAMPLE, "number": 1})
    real = AsyncSession.commit

    async def commit_then_fail(self: AsyncSession) -> None:
        await real(self)
        raise RuntimeError("подмена: сбой после фиксации")

    at_commit = "сбой на фиксации: RuntimeError — запись могла пройти, проверьте status"
    with monkeypatch.context() as patch:
        patch.setattr(AsyncSession, "commit", commit_then_fail)
        assert cli("load", F2, *signed(F2, 1)) == (1, "", at_commit + NL)
    assert status(capsys)[1].startswith("редакция 2 активна: файл, строк 7, порогов 1, sha256")


def test_a313_a_failure_outside_the_known_steps_sends_to_status(
    cli: Cli, monkeypatch: MP, capsys: Cap
) -> None:
    """A313: сбой вне опознанных шагов — закрытие движка бросает после записи (подмена): общий
    отказ шага не знает — «сбой записи: <тип> — проверьте status», а не «не тронут»; `status` —
    редакция 1 (сверка второй проверяющей среза 1, проба P6)."""
    real = AsyncEngine.dispose

    async def dispose_then_fail(self: AsyncEngine, close: bool = True) -> None:
        await real(self, close)
        raise RuntimeError("подмена: сбой после записи")

    with monkeypatch.context() as patch:
        patch.setattr(AsyncEngine, "dispose", dispose_then_fail)
        failed = cli("load", F, *signed(F, 0))
    assert failed == (1, "", "сбой записи: RuntimeError — проверьте status" + NL)
    assert status(capsys)[1].startswith("редакция 1 активна: файл, строк 6, порогов 1, sha256")


@contextlib.asynccontextmanager
async def holding(*items: list[dict[str, Any]]) -> AsyncIterator[None]:
    """Соединение теста: редакции `items` сырыми вставками, затем держит замок `REFERENCE` в своей
    транзакции; на выходе — снимает."""
    conn = await asyncpg.connect(dbtools._raw(dbtools.own_url()))
    try:
        for number, rows in enumerate(items, 1):
            await insert_revision(conn, rows, number=number)
        lock = conn.transaction()
        await lock.start()
        await conn.execute("SELECT pg_advisory_xact_lock($1)", locks.REFERENCE)
        try:
            yield
        finally:
            await lock.rollback()
    finally:
        await conn.close()


async def numbers() -> list[tuple[int, str]]:
    conn = await asyncpg.connect(dbtools._raw(dbtools.own_url()))
    try:
        found = await conn.fetch("SELECT number, status FROM reference_revisions ORDER BY number")
        return [(row["number"], row["status"]) for row in found]
    finally:
        await conn.close()


async def test_a314_two_loads_wait_for_the_lock_and_one_writes(committed: AsyncEngine) -> None:
    """A314: активна 1 из Ф; замок `REFERENCE` (ключ реестра) держит соединение теста; два
    `load --expect Т(1)` — Ф2 и Ф2 с долей 20 у `92` — ждут его (видно в `pg_locks`); замок
    снят — один даёт редакцию 2, второй — «справочник изменился…», ошибки базы в `__context__`
    нет; активна одна, номера 1 и 2."""
    assert locks.REGISTRY["reference"] == locks.REFERENCE
    other = dump(document(with92(SAMPLE2, share_percent=Decimal("20"))))
    found = [file.parse(data) for data in (F2, other)]
    async with holding(SAMPLE):
        tasks = [
            asyncio.create_task(store.load(committed, f, expect=(f.sha256, 1), reason="проба"))
            for f in found
        ]
        assert await dbtools.lock_waiters(locks.REFERENCE, at_least=2) == 2
    done = await asyncio.gather(*tasks, return_exceptions=True)
    written = [d.number for d in done if isinstance(d, store.Written)]
    refused = [(d.reasons, d.__context__) for d in done if isinstance(d, file.FileRefusedError)]
    assert (written, refused) == ([2], [([CHANGED.format(1, 2)], None)])
    assert await numbers() == [(1, "archived"), (2, "active")]


async def test_a314_two_loads_of_one_file_give_one_revision(committed: AsyncEngine) -> None:
    """A314: пустая база; два `load Ф --expect Т(0)` одного файла ждут замок — одна редакция 1,
    второй — «уже загружено»: двойную загрузку решает проверка под замком, а не до него."""
    found = file.parse(F)
    async with holding():
        tasks = [
            asyncio.create_task(store.load(committed, found, expect=(found.sha256, 0), reason="п"))
            for _ in range(2)
        ]
        assert await dbtools.lock_waiters(locks.REFERENCE, at_least=2) == 2
    done = await asyncio.gather(*tasks)
    assert sorted((d.number, d.already) for d in done) == [(1, False), (1, True)]
    assert await numbers() == [(1, "active")]


async def test_a314_a_load_gives_up_on_a_held_lock_after_the_limit(
    committed: AsyncEngine, monkeypatch: MP
) -> None:
    """A314: замок держит соединение теста; предел ожидания 1 с (подмена константы) — «сейчас
    пишет другая запись: ждали 1 с», база не тронута; замок снят — тот же `load` пишет."""
    monkeypatch.setattr(store, "LOCK_WAIT_SECONDS", 1)
    found = file.parse(F)
    busy = ["справочник сейчас пишет другая запись: ждали 1 с — повторите позже"]
    async with holding():
        with pytest.raises(file.FileRefusedError) as refused:
            await store.load(committed, found, expect=(found.sha256, 0), reason="проба")
        assert (refused.value.reasons, await numbers()) == (busy, [])
    written = await store.load(committed, found, expect=(found.sha256, 0), reason="проба")
    assert (written.number, await numbers()) == (1, [(1, "active")])
