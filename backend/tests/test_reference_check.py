"""`check` против базы (A304, A310, A312): «было — станет» против активной редакции, правки, которые
запись отменит, пропавшие строки, знак записи; вход в бою — проверка старта, подпроцессом (L123).
Редакции ставит помощник сырых вставок; после теста таблицы справочника пусты."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import dbtools
import pytest
from pydantic import ValidationError
from reference_sample import SAMPLE, SAMPLE2, document, dump, empty, files, manifest_of, revisions
from test_alerts_commands import process_env, run
from test_reference_file import MP, Cap, quiet

from ai_geo_audit import config
from ai_geo_audit.config import startup_checks
from ai_geo_audit.reference import __main__ as command
from ai_geo_audit.refusal import invalid_settings

NL = chr(10)
F, F2 = dump(document()), dump(document(SAMPLE2))
GOOD = "файл годен: строк {}, снимок 2026-11-18; с манифестом совпадает"
GONE = (
    "строки 95 нет в файле: строки справочника не удаляются — верните её в файл; снять проверку с"
)
Seed = Callable[..., list[tuple[int, str]]]
Run = Callable[[bytes], tuple[int, str, str]]


def sign(data: bytes, number: int) -> str:
    return f"{hashlib.sha256(data).hexdigest()}@{number}"


#: Строка готовой команды `load` перед знаком (Ч2.1б, A311); путь файла — `<файл>`.
COMMAND = (
    "запись: python -m ai_geo_audit.reference load <файл> --expect {} --reason"
    ' "<чьё слово и когда>"'
)


def tail(data: bytes, number: int) -> list[str]:
    """Последние строки вывода `check`: готовая команда `load`, знак записи и пустая строка."""
    return [COMMAND.format(sign(data, number)), sign(data, number), ""]


@pytest.fixture
def seed(monkeypatch: MP) -> Iterator[Seed]:
    """Адрес базы команды — тестовая база; `seed(...)` ставит редакции и отдаёт список всех; после
    теста таблицы справочника пусты."""
    monkeypatch.setattr(config, "storage", config.StorageSettings(database_url=dbtools.own_url()))
    yield revisions
    empty()


@pytest.fixture
def check(tmp_path: Path, monkeypatch: MP, capsys: Cap) -> Run:
    """`check` в процессе теста по байтам файла и его манифесту: код, вывод, поток ошибок; путь
    файла в выводе — `<файл>`: его называет строка готовой команды `load`."""
    quiet(monkeypatch)

    def run_check(data: bytes) -> tuple[int, str, str]:
        path, spec = files(tmp_path, data, manifest_of(data, len(json.loads(data)["rows"])))
        code = command.main(["check", path, "--manifest", spec])
        out, err = capsys.readouterr()
        return code, out.replace(path, "<файл>"), err

    return run_check


def with92(rows: list[dict[str, Any]], **fields: Any) -> list[dict[str, Any]]:
    """Копия строк Ф или Ф2, у `92` (вторая строка) — другие поля."""
    found = [dict(item) for item in rows]
    found[1] |= fields
    return found


SLOW = [{"name": "max_seconds", "value": Decimal("3.5"), "unit": "s"}]
EDITED = with92(SAMPLE, params=SLOW)


def test_a304_without_revisions_all_rows_are_new_and_the_sign_is_zero(
    seed: Seed, check: Run
) -> None:
    """A304: редакций нет, `check` Ф — код 0, «новых строк 6», последняя строка — Т(0)."""
    assert seed() == []
    assert check(F) == (0, NL.join([GOOD.format(6), "новых строк 6", *tail(F, 0)]), "")
    assert seed() == []


def test_a304_changes_against_the_active_revision(seed: Seed, check: Run) -> None:
    """A304: активна 1 из Ф, `check` Ф2 — доля `92`, норма `93` длиной без текста, новая строка
    `95`, последняя строка — Т(1); база не тронута."""
    before = seed({"rows": SAMPLE, "number": 1})
    norm = (
        f"93 · норма: текст изменён, было {len('норма 93')} знаков, стало {len('другая норма 93')}"
    )
    lines = [GOOD.format(7), "92 · доля: 10 % → 15 %", norm, "новая строка 95", *tail(F2, 1)]
    assert check(F2) == (0, NL.join(lines), "")
    assert seed() == before == [(1, "active")]


def test_a304_edits_the_file_would_cancel_are_named(seed: Seed, check: Run) -> None:
    """A304: активна 2 — правка поверх 1 из Ф (у `92` `max_seconds` 3.5): `check` Ф называет
    правку, которую запись отменит; последняя строка — Т(2); база не тронута."""
    first = {"rows": SAMPLE, "number": 1, "status": "archived"}
    before = seed(first, {"rows": EDITED, "number": 2, "source": "edit"})
    cancelled = "будут отменены правки редакции 2: 92 · max_seconds 3.5 → 3.25 с"
    assert check(F) == (0, NL.join([GOOD.format(6), cancelled, *tail(F, 2)]), "")
    assert seed() == before


def test_a304_edits_count_from_the_last_file_revision(seed: Seed, check: Run) -> None:
    """A304: 1 из Ф и 2 из Ф2 в архиве, активна 3 — правка поверх 2 (у `92` `max_seconds` 3.5):
    `check` Ф2 с долей `92` 20 — доля без пометки (её сменил файл 2, а не правка), порог — с
    пометкой: правки считаются от последней редакции из файла, а не от первой (находка 1)."""
    old = {"status": "archived"}
    edit = {"rows": with92(SAMPLE2, params=SLOW), "number": 3, "source": "edit"}
    seed({"rows": SAMPLE, "number": 1} | old, {"rows": SAMPLE2, "number": 2} | old, edit)
    data = dump(document(with92(SAMPLE2, share_percent=Decimal("20"))))
    cancelled = "будут отменены правки редакции 3: 92 · max_seconds 3.5 → 3.25 с"
    lines = [GOOD.format(7), "92 · доля: 15 % → 20 %", cancelled, *tail(data, 3)]
    assert check(data) == (0, NL.join(lines), "")


MS = [{"name": "max_seconds", "value": Decimal("3250"), "unit": "ms"}]
CLS = [*SAMPLE[1]["params"], {"name": "cls", "value": Decimal("0.175"), "unit": "number"}]


@pytest.mark.parametrize(
    ("rows", "line"),
    [
        ([SAMPLE[1], SAMPLE[0], *SAMPLE[2:]], "порядок: строк на других местах 2"),
        ([*SAMPLE[:2], SAMPLE2[-1], *SAMPLE[2:]], "новая строка 95"),
        (with92(SAMPLE, params=MS), "92 · max_seconds 3.25 с → 3250 мс"),
        (with92(SAMPLE, params=[]), "92 · порог max_seconds убран"),
        (with92(SAMPLE, params=CLS), "92 · cls новый, 0.175"),
    ],
    ids=["swapped", "inserted-in-the-middle", "unit", "removed", "new-number"],
)
def test_a304_order_and_threshold_changes_are_named(
    seed: Seed, check: Run, rows: list[dict[str, Any]], line: str
) -> None:
    """A304: активна 1 из Ф. Строки 1 и 2 переставлены — «порядок: …» без кодов: места в базе
    другие, «изменений нет» было бы неправдой. Строка `95` в середине сдвигает места ниже, но
    порядок общих строк прежний — только «новая строка». Смена единицы — со старой единицей:
    «3.25 → 3250 мс» читалось бы ростом в тысячу раз. Порог убран и порог новый — по имени; у
    единицы `number` слова нет."""
    seed({"rows": SAMPLE, "number": 1})
    data = dump(document(rows))
    assert check(data) == (0, NL.join([GOOD.format(len(rows)), line, *tail(data, 1)]), "")


def test_a304_enum_changes_are_words_and_a_missing_share_is_a_dash(seed: Seed, check: Run) -> None:
    """A304: активна 1 из Ф; у `91` в файле важность «Важно», область «все страницы», доля 10 —
    перечисления называются словами, а не значениями, нет доли у сайта — «—»."""
    seed({"rows": SAMPLE, "number": 1})
    moved = {"importance": "important", "scope": "pages", "share_percent": Decimal("10")}
    data = dump(document([SAMPLE[0] | moved, *SAMPLE[1:]]))
    words = ["91 · важность: Критично → Важно", "91 · область: сайт → все страницы"]
    lines = [GOOD.format(6), *words, "91 · доля: — → 10 %", *tail(data, 1)]
    assert check(data) == (0, NL.join(lines), "")


def test_a304_revision_rows_are_read_by_position_not_by_insertion(seed: Seed, check: Run) -> None:
    """A304: строки редакции 1 вставлены не по порядку мест — `91` на месте 2, `92` на месте 1:
    `check` Ф называет порядок — строки редакции читаются по месту, а не по порядку вставки."""
    seed({"rows": SAMPLE, "number": 1, "positions": [2, 1, 3, 4, 5, 6]})
    lines = [GOOD.format(6), "порядок: строк на других местах 2", *tail(F, 1)]
    assert check(F) == (0, NL.join(lines), "")


SHARES = F.replace(b'"share_percent": 10,', b'"share_percent": 10.0,')


@pytest.mark.parametrize(
    "data", [F, F.replace(b"3.25", b"3.250"), SHARES], ids=["same", "3.250", "share-10.0"]
)
def test_a304_a_file_equal_to_the_revision_has_no_changes(
    seed: Seed, check: Run, data: bytes
) -> None:
    """A304: активна 1 из Ф; `check` Ф, Ф с записью порога `3.250` и Ф с долями `10.0` —
    «изменений нет: файл равен редакции 1»: числа сравниваются значением, а не записью."""
    seed({"rows": SAMPLE, "number": 1})
    lines = [GOOD.format(6), "изменений нет: файл равен редакции 1", *tail(data, 1)]
    assert check(data) == (0, NL.join(lines), "")


def test_a312_a_row_missing_from_the_file_is_refused_without_a_sign(seed: Seed, check: Run) -> None:
    """A312: активна редакция из Ф2, в Ф нет `95` — код 1, причина с подсказкой, знака нет."""
    before = seed({"rows": SAMPLE2, "number": 1})
    assert check(F) == (1, "", f"{GONE} аудитов — убрать её из наборов" + NL)
    assert seed() == before


async def test_a310_an_empty_reference_is_refused_not_guessed(session: Any) -> None:
    """A310: справочник пуст — `active_revision()` отказывает словами, а не берёт умолчания."""
    from ai_geo_audit.reference import store  # здесь: без кода среза тест красный сам

    with pytest.raises(store.NotLoadedError) as refused:
        await store.active_revision(session)
    assert (
        str(refused.value) == "справочник не загружен — загрузка: python -m ai_geo_audit.reference"
    )


def test_a310_the_reference_process_has_the_app_role_and_the_api_list() -> None:
    """A310: процесс `reference` — роль `audit_app`, список `DATABASE_URL` — внутри списка `api`:
    команда идёт в контейнере api."""
    assert startup_checks.DB_ROLES["reference"] == "audit_app"
    assert startup_checks.required("reference") == {"DATABASE_URL"}
    assert startup_checks.required("reference") <= startup_checks.required("api")


#: Адрес базы с ролью миграций; пароля не должно быть в выводе.
LONG = "pw-1234567890"
# pragma: allowlist nextline secret
MIGRATE_URL = f"postgresql+asyncpg://audit_migrate:{LONG}@db:5432/audit"
STARTUP = "Процесс «reference» не стартует в боевом режиме:"
DEFAULT = "  DATABASE_URL — в бою задаётся явно: умолчание ведёт на машину разработчика"


def xml_refusal(monkeypatch: MP) -> str:
    """Отказ негодной настройки `LOG_FORMAT=xml` — тем же путём, что у входа (L30)."""
    monkeypatch.setenv("LOG_FORMAT", "xml")
    with pytest.raises(ValidationError) as invalid:
        config.LogSettings()
    return invalid_settings("reference", invalid.value)


@pytest.mark.parametrize(
    "case", ["no-database-url", "migrate-role", "log-format-xml"], ids=lambda case: case
)
def test_a310_in_prod_the_command_does_not_start_without_its_settings(
    tmp_path: Path, monkeypatch: MP, case: str
) -> None:
    """A310: `APP_ENV=prod`, вход подпроцессом `python -m`: без `DATABASE_URL`, с ролью миграций,
    с `LOG_FORMAT=xml` — код 1, причина словами с именем переменной, целиком; адреса, пароля,
    введённого значения и `Traceback` нет во всём выводе (L29, L30, L123)."""
    foreign = "  DATABASE_URL — в адресе чужая роль базы — нужна audit_app"
    extra, err = {
        "no-database-url": ({}, f"{STARTUP}{NL}{DEFAULT}"),
        "migrate-role": ({"DATABASE_URL": MIGRATE_URL}, f"{STARTUP}{NL}{foreign}"),
        "log-format-xml": ({"LOG_FORMAT": "xml"}, xml_refusal(monkeypatch)),
    }[case]
    path, _ = files(tmp_path, F, {})
    done = run(
        ["-m", "ai_geo_audit.reference", "check", path], process_env(APP_ENV="prod", **extra)
    )
    assert (done.returncode, done.stdout, done.stderr) == (1, "", err + NL)
    assert all(mark not in done.stderr for mark in ("Traceback", LONG, "xml", "@db"))
