"""Файл справочника, манифест и `check` до базы (A303, A305–A308): все причины за прогон своими
словами, без значений и текстов файла (A307). Журналы и крючки — заглушкой, их держит подпроцесс
A310; порядок шагов входа — тест ниже (L55). Знаки вне ASCII — через `chr` и `bytes` (L128);
ожидание — целая строка «строка, код: причина» (L124)."""

from __future__ import annotations

import codecs
import errno
import hashlib
import io
import os
import re
from collections.abc import Callable
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import dbtools
import pytest
from pydantic import ValidationError
from reference_sample import SAMPLE, SAMPLE2, document, dump, files, manifest_of, row

from ai_geo_audit import config
from ai_geo_audit.reference import __main__ as command
from ai_geo_audit.reference import file, manifest

Run = Callable[..., tuple[int, str, str]]
MP, Log, Cap = pytest.MonkeyPatch, pytest.LogCaptureFixture, pytest.CaptureFixture[str]
NL = chr(10)
GOOD = "файл годен: строк 6, снимок 2026-11-18; с манифестом совпадает"
MARK = "customer-text-mark-words"
SAMPLE_BYTES = dump(document())
R1, R2, R3 = "строка 1, код 91: ", "строка 2, код 92: ", "строка 3, код 92.1: "
CODE = "строка 2: код — номер вида 7 или 1.3"
SHARE = R2 + "доля — больше 0 и не больше 100"
BOUNDS = R2 + "max_seconds вне пределов единицы s: 0.1–60"
PARAM_NAME = R2 + "порог 1: имя — латиница a–z, цифры и _, с буквы, до 40 знаков"
NOT_NUMBER = "— число, а не true или false"
UNITS = "s, ms, mb, percent, chars, clicks, count или number"
IMPORTANCE = "важность — critical, important или low"
COVERAGE = "охват — full, partial или no_data"
INFINITE = "в файле NaN или Infinity — числа только конечные"
DATE = "дата снимка — вида 2026-11-18"
FORMAT = "формат не поддерживается — нужен 1"
UNREADABLE = "манифест не читается или не того вида: format, rows, sha256, source_date"
REPEATED = "повторяется поле: format"
SCOPES = "site, pages или sample"
P95 = R2 + "p95_ms вне пределов единицы s: 0.1–60"


def quiet(monkeypatch: MP, calls: list[str] | None = None) -> None:
    """Журналы, крючки и проверка старта — заглушкой (их держит подпроцесс A310), `calls` пишет
    их порядок; база команды — тестовая."""
    seen = calls if calls is not None else []
    monkeypatch.setattr(command, "setup_logging", lambda: seen.append("logs"))
    monkeypatch.setattr(command, "guard_process", lambda: seen.append("guard"))
    monkeypatch.setattr(command, "check_startup", seen.append)
    monkeypatch.setattr(config, "storage", config.StorageSettings(database_url=dbtools.own_url()))


@pytest.fixture
def check(tmp_path: Path, monkeypatch: MP, capsys: Cap) -> Run:
    """`check` в процессе теста по байтам файла и манифесту к ним: код, вывод, поток ошибок."""
    quiet(monkeypatch)

    def run(data: bytes, rows: int = 6, **changes: Any) -> tuple[int, str, str]:
        path, spec = files(tmp_path, data, manifest_of(data, rows, **changes))
        code = command.main(["check", path, "--manifest", spec])
        out, err = capsys.readouterr()
        return code, out, err

    return run


def with_row(n: int, **fields: Any) -> bytes:
    """Ф, где у строки `n` (с 1) поля `fields`."""
    rows = [dict(item) for item in SAMPLE]
    rows[n - 1] |= fields
    return dump(document(rows))


def r2(**fields: Any) -> bytes:
    return with_row(2, **fields)


def without(n: int, key: str) -> bytes:
    """Ф, где у строки `n` нет поля `key`."""
    rows = [dict(item) for item in SAMPLE]
    del rows[n - 1][key]
    return dump(document(rows))


def instead(n: int, value: object) -> bytes:
    """Ф, где строка `n` — `value` вместо объекта."""
    rows: list[object] = [dict(item) for item in SAMPLE]
    rows[n - 1] = value
    return dump(document(rows))


def param(**changes: Any) -> dict[str, Any]:
    """Порог `92` из Ф с правками."""
    return {"name": "max_seconds", "value": Decimal("3.25"), "unit": "s"} | changes


NOT_UTF8 = SAMPLE_BYTES.replace("блок 91".encode(), bytes([0xFF, 0xFE]))
NOT_JSON = NL.join(["{", ' "format": 1,', ' "ab" [', ""]).encode()
NOT_LATIN_KEY = "лишнее поле верхнего уровня с именем не латиницей"
#: Порча файла: имя, байты, поток ошибок без последнего перевода строки.
FILE_CASES: list[tuple[str, bytes, str]] = [
    ("not-utf8", NOT_UTF8, "файл не в UTF-8"),
    ("bom", codecs.BOM_UTF8 + SAMPLE_BYTES, "в начале файла метка BOM — сохраните файл без неё"),
    ("not-json", NOT_JSON, "файл — не JSON: строка 3, знак 7"),
    ("nan", SAMPLE_BYTES.replace(b"3.25", b"NaN"), INFINITE),
    ("minus-infinity", SAMPLE_BYTES.replace(b"3.25", b"-Infinity"), INFINITE),
    ("extra-field", dump(document(extra=1)), "лишнее поле верхнего уровня: extra"),
    ("extra-not-latin", dump(document() | {"поле": 1}), NOT_LATIN_KEY),
    ("no-rows", dump({"format": 1, "source_date": "2026-11-18"}), "нет поля rows"),
    ("no-format-no-date", dump({"rows": SAMPLE}), "нет поля format" + NL + "нет поля source_date"),
    ("format-2", dump(document(format=2)), "формат 2 не поддерживается — нужен 1"),
    ("format-true", dump(document(format=True)), FORMAT),
    ("format-text", dump(document(format=MARK)), FORMAT),
    ("rows-empty", dump(document([])), "в файле нет строк"),
    ("rows-not-list", dump(document() | {"rows": "строки"}), "rows — нужен список строк"),
    ("not-object", dump([1]), "файл — не объект с полями format, source_date и rows"),
    ("date-otherwise", dump(document(source_date="18.11.2026")), DATE),
    ("date-impossible", dump(document(source_date="2026-13-01")), DATE),
    ("date-number", dump(document(source_date=20261118)), DATE),
    ("date-digits-only", dump(document(source_date="20261118")), DATE),
    ("format-1000", dump(document(format=1000)), FORMAT),
    ("repeated-top", SAMPLE_BYTES.replace(b'"format": 1,', b'"format": 1, "format": 1,'), REPEATED),
]


@pytest.mark.parametrize(("data", "reasons"), [pytest.param(d, r, id=i) for i, d, r in FILE_CASES])
def test_a305_a_spoilt_file_is_refused_with_its_reasons(
    check: Run, data: bytes, reasons: str
) -> None:
    """A305: порча файла, каждая отдельно — не UTF-8, BOM, не JSON с местом, `NaN`, лишнее поле, нет
    `rows`, формат 2, пустые `rows` — и соседи (L53): код 1, причины целиком, вывод пуст."""
    assert check(data) == (1, "", reasons + NL)


NOT_LATIN_ROW = dump(document([*SAMPLE[:1], SAMPLE[1] | {"поле": 1}, *SAMPLE[2:]]))
NUL = R2 + "норма — в тексте нулевой знак: база такой текст не примет"
METHODS = R2 + "способ — rules, rules_model, model, platforms или external"
AT_SITE = R1 + "доля — только у проверок по страницам и выборке"
NO_UNIT = {"name": "max_seconds", "value": Decimal("3.25")}
ALMOST_LATIN = dump(document([*SAMPLE[:1], SAMPLE[1] | {"aполе": 1}, *SAMPLE[2:]]))
BLOCK_92 = '"block": "блок 92"'
REPEATED_ROW = SAMPLE_BYTES.replace(BLOCK_92.encode(), f"{BLOCK_92}, {BLOCK_92}".encode())
REPEATED_PARAM = SAMPLE_BYTES.replace(b'"unit": "s"', b'"unit": "s", "unit": "s"')
REPEATED_CYRILLIC = SAMPLE_BYTES.replace(BLOCK_92.encode(), f'"п": 1, "п": 2, {BLOCK_92}'.encode())
SCHEMA_AND_RULE = with_row(1, importance="urgent", share_percent=Decimal(10))
NOT_LATIN_PARAM = r2(params=[param(name="порог_м", unit="furlong")])
UNIT_AND_BOUNDS = r2(params=[param(unit="furlong"), param(name="p95_ms", value=Decimal(61))])
CYRILLIC = "лишнее поле с именем не латиницей"
#: Порча строки Ф: имя, байты, поток ошибок без последнего перевода строки.
ROW_CASES: list[tuple[str, bytes, str]] = [
    ("code-again", r2(code="91"), "строка 2, код 91: код уже был в строке 1"),
    ("code-9a", r2(code="9a"), CODE),
    ("code-other-digits", r2(code=chr(0x669) + chr(0x662)), CODE),
    ("code-wide-digits", r2(code=chr(0xFF19) + chr(0xFF12)), CODE),
    ("code-newline", r2(code="92" + NL), CODE),
    ("code-123", r2(code="123"), CODE),
    ("code-9.123", r2(code="9.123"), CODE),
    ("code-missing", without(2, "code"), CODE),
    ("norm-empty", r2(norm=""), R2 + "норма пустая"),
    ("error-empty", r2(error=""), R2 + "ошибка пустая"),
    ("block-empty", r2(block=""), R2 + "блок пустой"),
    ("name-empty", r2(name=""), R2 + "имя пустое"),
    ("recommendation-4001", r2(recommendation="р" * 4001), R2 + "рекомендация длиннее 4000 знаков"),
    ("name-201", r2(name="и" * 201), R2 + "имя длиннее 200 знаков"),
    ("block-201", r2(block="б" * 201), R2 + "блок длиннее 200 знаков"),
    ("block-number", r2(block=Decimal(5)), R2 + "блок — нужен текст"),
    ("norm-nul", r2(norm="норма" + chr(0)), NUL),
    ("importance-urgent", r2(importance="urgent"), R2 + IMPORTANCE),
    ("method-magic", r2(method="magic"), METHODS),
    ("coverage-maybe", r2(coverage="maybe"), R2 + COVERAGE),
    ("scope-everywhere", r2(scope="everywhere"), R2 + "область — site, pages или sample"),
    ("share-at-site", with_row(1, share_percent=Decimal(10)), AT_SITE),
    ("sample-without-share", with_row(3, share_percent=None), R3 + "у выборки нужна доля"),
    ("pages-without-share", r2(share_percent=None), R2 + "у проверки по всем страницам нужна доля"),
    ("share-0", r2(share_percent=Decimal(0)), SHARE),
    ("share-100.5", r2(share_percent=Decimal("100.5")), SHARE),
    ("share-true", r2(share_percent=True), R2 + "доля " + NOT_NUMBER),
    ("share-text", r2(share_percent="10"), R2 + "доля — нужно число"),
    ("param-61", r2(params=[param(value=Decimal(61))]), BOUNDS),
    ("param-0.05", r2(params=[param(value=Decimal("0.05"))]), BOUNDS),
    ("param-true", r2(params=[param(value=True)]), R2 + "max_seconds " + NOT_NUMBER),
    ("param-text", r2(params=[param(value="3.25")]), R2 + "max_seconds — нужно число"),
    ("unit-furlong", r2(params=[param(unit="furlong")]), R2 + "max_seconds: единица — " + UNITS),
    ("param-twice", r2(params=[param(), param()]), R2 + "порог max_seconds повторяется"),
    ("param-name-Max", r2(params=[param(name="Max")]), PARAM_NAME),
    ("param-name-41", r2(params=[param(name="a" * 41)]), PARAM_NAME),
    ("param-name-number", r2(params=[param(name=Decimal(5))]), R2 + "порог 1: имя — нужен текст"),
    ("param-without-unit", r2(params=[NO_UNIT]), R2 + "max_seconds: нет поля unit"),
    ("param-extra", r2(params=[param(note=1)]), R2 + "max_seconds: лишнее поле: note"),
    ("param-not-object", r2(params=[5]), R2 + "порог 1 — нужен объект"),
    ("params-not-list", r2(params="пороги"), R2 + "пороги — нужен список"),
    ("row-extra", r2(extra=1), R2 + "лишнее поле: extra"),
    ("row-extra-not-latin", NOT_LATIN_ROW, R2 + "лишнее поле с именем не латиницей"),
    ("row-without-norm", without(2, "norm"), R2 + "нет поля norm"),
    ("row-not-object", instead(2, 5), "строка 2 — не объект"),
    ("row-extra-almost-latin", ALMOST_LATIN, R2 + CYRILLIC),
    (
        "block-nul",
        r2(block="блок" + chr(0)),
        R2 + "блок — в тексте нулевой знак: база такой текст не примет",
    ),
    ("param-not-latin", NOT_LATIN_PARAM, R2 + "порог 1: единица — " + UNITS + NL + PARAM_NAME),
    ("schema-and-rule", SCHEMA_AND_RULE, R1 + IMPORTANCE + NL + AT_SITE),
    ("unit-and-bounds", UNIT_AND_BOUNDS, R2 + "max_seconds: единица — " + UNITS + NL + P95),
    ("scope-no-cascade", r2(scope="everywhere", share_percent=None), R2 + "область — " + SCOPES),
    ("site-share-text-no-cascade", with_row(1, share_percent="10"), R1 + "доля — нужно число"),
    (
        "code-and-importance",
        r2(code="9a", importance="urgent"),
        CODE + NL + "строка 2: " + IMPORTANCE,
    ),
    ("repeated-row-key", REPEATED_ROW, R2 + REPEATED.replace("format", "block")),
    (
        "repeated-param-key",
        REPEATED_PARAM,
        R2 + "max_seconds: " + REPEATED.replace("format", "unit"),
    ),
    (
        "repeated-cyrillic",
        REPEATED_CYRILLIC,
        R2 + "повторяется поле с именем не латиницей" + NL + R2 + CYRILLIC,
    ),
]


@pytest.mark.parametrize(("data", "reasons"), [pytest.param(d, r, id=i) for i, d, r in ROW_CASES])
def test_a305_a_spoilt_row_is_refused_with_its_reasons(
    check: Run, data: bytes, reasons: str
) -> None:
    """A305: порча в строке Ф, каждая отдельно — код, тексты, перечисления, доля, пороги — и соседи
    каждого условия (L53): код 1, причины целиком, со строкой и кодом, без значений."""
    assert check(data) == (1, "", reasons + NL)


def test_a305_three_spoilt_rows_give_three_reasons_in_order(check: Run) -> None:
    """A305: порча в строках 1, 2 и 3 — три причины за один прогон, в порядке строк."""
    rows = [dict(item) for item in SAMPLE]
    rows[0] |= {"importance": "urgent"}
    rows[1] |= {"norm": ""}
    rows[2] |= {"coverage": "maybe"}
    reasons = [R1 + IMPORTANCE, R2 + "норма пустая", R3 + COVERAGE, ""]
    assert check(dump(document(rows))) == (1, "", NL.join(reasons))


GOOD_CASES = {
    "share-100": r2(share_percent=Decimal(100)),
    "share-0.01": r2(share_percent=Decimal("0.01")),
    "value-60": r2(params=[param(value=Decimal(60))]),
    "value-0.1": r2(params=[param(value=Decimal("0.1"))]),
    "param-name-with-digit": r2(params=[param(name="p95_ms")]),
    "param-name-40": r2(params=[param(name="a" * 40)]),
    "two-params": r2(params=[param(), param(name="p95_ms", value=500, unit="ms")]),
    "names-200": r2(name="и" * 200, block="б" * 200),
    "texts-4000": r2(norm="н" * 4000, error="о" * 4000, recommendation="р" * 4000),
    "code-9": with_row(4, code="9"),
    "code-92.12": with_row(4, code="92.12"),
    "value-written-3.250": SAMPLE_BYTES.replace(b"3.25", b"3.250"),
}


@pytest.mark.parametrize("data", list(GOOD_CASES.values()), ids=list(GOOD_CASES))
def test_a305_good_neighbours_pass(check: Run, data: bytes) -> None:
    """A305, соседи (L53): доля 100 и 0.01, порог на пределах единицы, имя порога с цифрой и в 40
    знаков, два порога, имя и блок в 200, тексты в 4000, коды `9` и `92.12`, запись `3.250`."""
    code, out, err = check(data)
    assert (code, err, out.split(NL)[0]) == (0, "", GOOD)


def test_a305_the_file_comes_from_standard_input_too(
    tmp_path: Path, monkeypatch: MP, capsys: Cap
) -> None:
    """A305: файл `-` — со стандартного ввода, байтами, как в бою через `-T`."""
    quiet(monkeypatch)
    _, spec = files(tmp_path, SAMPLE_BYTES, manifest_of(SAMPLE_BYTES, 6))
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(SAMPLE_BYTES)))
    assert command.main(["check", "-", "--manifest", spec]) == 0
    assert capsys.readouterr().out.split(NL)[0] == GOOD


def test_a305_a_file_that_does_not_open_is_named_without_its_path(
    tmp_path: Path, monkeypatch: MP, capsys: Cap
) -> None:
    """A305: файла нет — причина словами системы, пути в ней нет."""
    quiet(monkeypatch)
    _, spec = files(tmp_path, SAMPLE_BYTES, manifest_of(SAMPLE_BYTES, 6))
    assert command.main(["check", str(tmp_path / f"{MARK}.json"), "--manifest", spec]) == 1
    assert capsys.readouterr() == ("", f"файл не открылся: {os.strerror(errno.ENOENT)}" + NL)


#: Коды строк в пределах 90–99, хватает на 297 строк.
CODES = [f"{head}.{tail}" for head in (90, 91, 92) for tail in range(1, 100)]


def many(n: int) -> bytes:
    return dump(document([row(code) for code in CODES[:n]]))


def padded(size: int) -> bytes:
    """Ф с пробелами после JSON до `size` байт."""
    return SAMPLE_BYTES + b" " * (size - len(SAMPLE_BYTES))


def test_a306_a_file_over_1_mb_is_not_read_further(check: Run) -> None:
    """A306: 1 048 577 байт — «файл больше 1 МБ — дальше не читается»."""
    assert check(padded(1_048_577)) == (1, "", "файл больше 1 МБ — дальше не читается" + NL)


def test_a306_the_stream_is_not_read_past_the_limit() -> None:
    """A306: поток в 3 МБ — отказ; прочитано ровно 1 048 577 байт: дальше не читается."""
    stream = io.BytesIO(b" " * 3_145_728)
    with pytest.raises(file.FileRefusedError) as refused:
        file.read(stream)
    reason = ["файл больше 1 МБ — дальше не читается"]
    assert (refused.value.reasons, stream.tell()) == (reason, 1_048_577)


def test_a306_more_than_200_rows_are_refused(check: Run) -> None:
    """A306: 201 строка — «строк больше 200»."""
    assert check(many(201), rows=201) == (1, "", "строк больше 200" + NL)


def test_a306_a_file_on_both_limits_passes(check: Run) -> None:
    """A306: на пределе — 1 048 576 байт и 200 строк — причины о размере и числе строк нет."""
    code, out, err = check(padded(1_048_576))
    assert (code, err, out.split(NL)[0]) == (0, "", GOOD)
    code, out, err = check(many(200), rows=200)
    wide = "файл годен: строк 200, снимок 2026-11-18; с манифестом совпадает"
    assert (code, err, out.split(NL)[0]) == (0, "", wide)


def test_a307_texts_of_the_file_are_nowhere_in_the_output(check: Run, caplog: Log) -> None:
    """A307: норма `93` — метка при важности `urgent`, тот же файл не в UTF-8, метка посреди JSON:
    причина — строкой, кодом и полем; метки и `Traceback` нет нигде, журнал тоже."""
    rows = [dict(item) for item in SAMPLE]
    rows[3] |= {"norm": MARK, "importance": "urgent"}
    marked = dump(document(rows))
    broken = b'{"rows": "' + MARK.encode() + b'" x'
    results = [check(marked), check(marked.replace(b"3.25", bytes([0xFF]))), check(broken)]
    assert [(code, err) for code, _, err in results] == [
        (1, "строка 4, код 93: " + IMPORTANCE + NL),
        (1, "файл не в UTF-8" + NL),
        (1, f"файл — не JSON: строка 1, знак {len(broken)}" + NL),
    ]
    text = "".join(out + err for _, out, err in results) + caplog.text
    assert (MARK in text, "Traceback" in text) == (False, False)


def test_a307_an_unexpected_failure_is_one_line_with_its_type(
    check: Run, monkeypatch: MP, caplog: Log
) -> None:
    """A307: подмена бросает исключение с меткой — строка «сбой проверки: <тип>…», метки нет."""

    def failing(data: bytes) -> file.ReferenceFile:
        raise RuntimeError(f"сбой с текстом файла: {MARK}")

    monkeypatch.setattr(file, "parse", failing)
    code, out, err = check(SAMPLE_BYTES)
    assert (code, out, err) == (1, "", "сбой проверки: RuntimeError — справочник не тронут" + NL)
    assert (MARK in caplog.text, "Traceback" in caplog.text) == (False, False)


def test_a307_logs_and_hooks_come_before_the_file(tmp_path: Path, monkeypatch: MP) -> None:
    """A307: журналы, крючки процесса и проверка старта — до чтения файла (L54, L55)."""
    calls: list[str] = []
    quiet(monkeypatch, calls)
    read = file.read

    def traced(stream: Any) -> bytes:
        calls.append("read")
        return read(stream)

    monkeypatch.setattr(file, "read", traced)
    path, spec = files(tmp_path, SAMPLE_BYTES, manifest_of(SAMPLE_BYTES, 6))
    assert command.main(["check", path, "--manifest", spec]) == 0
    assert calls == ["logs", "guard", "reference", "read"]


@pytest.mark.parametrize(
    "argv",
    [[], ["load", "-"], ["check"], ["check", "a", "b"], ["check", "a", "--manifest"]],
    ids=["none", "load", "no-file", "two-files", "no-manifest-path"],
)
def test_a307_other_arguments_are_the_usage_line(
    monkeypatch: MP, capsys: Cap, argv: list[str]
) -> None:
    """A307: иной вид аргументов — строка использования, код 2; журналы, крючки, файл не тронуты."""
    calls: list[str] = []
    quiet(monkeypatch, calls)
    assert command.main(argv) == 2
    assert (capsys.readouterr(), calls) == (("", command.USAGE + NL), [])


def test_a308_a_file_unlike_its_manifest_is_refused(check: Run) -> None:
    """A308: Ф годен, манифест — строк 7; манифест — другой sha256."""
    other = "файл не совпадает с манифестом: sha256 другой — манифест описывает другой файл"
    assert [check(SAMPLE_BYTES, rows=7), check(SAMPLE_BYTES, sha256="0" * 64)] == [
        (1, "", "файл не совпадает с манифестом: строк 6, в манифесте 7" + NL),
        (1, "", other + NL),
    ]


def in_prod(monkeypatch: MP) -> None:
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setattr(config, "app", config.AppSettings())


def test_a308_in_prod_only_the_manifest_of_the_image(check: Run, monkeypatch: MP) -> None:
    """A308: `APP_ENV=prod` и `--manifest` — «в бою — только манифест из образа»."""
    in_prod(monkeypatch)
    assert check(SAMPLE_BYTES) == (1, "", "в бою — только манифест из образа" + NL)


def image_check(tmp_path: Path, monkeypatch: MP, image: dict[str, Any]) -> int:
    """`check` Ф в бою без `--manifest`: манифест из образа — подмена `image`."""
    in_prod(monkeypatch)
    quiet(monkeypatch)
    monkeypatch.setattr(manifest, "MANIFEST", manifest.Manifest(**image))
    path, _ = files(tmp_path, SAMPLE_BYTES, {})
    return command.main(["check", path])


def test_a308_an_empty_manifest_of_the_image_is_refused_in_prod(
    tmp_path: Path, monkeypatch: MP, capsys: Cap
) -> None:
    """A308: в бою манифест из образа с пустым хешем (подмена) — «манифест не заполнен…»."""
    empty = {"format": 1, "rows": 58, "sha256": "", "source_date": ""}
    assert image_check(tmp_path, monkeypatch, empty) == 1
    assert capsys.readouterr() == ("", "манифест не заполнен — файла ведущей в нём ещё нет" + NL)


def test_a308_the_manifest_of_the_image_is_the_one_in_prod(
    tmp_path: Path, monkeypatch: MP, capsys: Cap
) -> None:
    """A308: `APP_ENV=prod`, в образе — манифест этого файла (подмена): годен без `--manifest`."""
    assert image_check(tmp_path, monkeypatch, manifest_of(SAMPLE_BYTES, 6)) == 0
    out, err = capsys.readouterr()
    assert (err, out.split(NL)[0]) == ("", GOOD)


MANIFEST_CASES: dict[str, dict[str, Any]] = {
    "sha-63": {"sha256": "a" * 63},
    "sha-65": {"sha256": "a" * 65},
    "sha-upper": {"sha256": "A" * 64},
    "sha-newline": {"sha256": "a" * 64 + NL},
    "sha-without-date": {"source_date": ""},
    "date-without-sha": {"sha256": ""},
    "date-otherwise": {"source_date": "18.11.2026"},
    "rows-0": {"rows": 0},
    "rows-text": {"rows": "58"},
    "extra": {"extra": 1},
}
GOOD_MANIFEST = {"format": 1, "rows": 58, "sha256": "a" * 64, "source_date": "2026-11-18"}


@pytest.mark.parametrize("changes", list(MANIFEST_CASES.values()), ids=list(MANIFEST_CASES))
def test_a303_a_manifest_of_another_form_is_refused(changes: dict[str, Any]) -> None:
    """A303: хеш не из 64 знаков `0-9a-f`, хеш без даты и дата без хеша, дата не ISO, строк 0 или
    строкой, лишнее поле — отказ при сборке; годны оба пустых и оба заполненных."""
    with pytest.raises(ValidationError):
        manifest.Manifest(**(GOOD_MANIFEST | changes))
    assert manifest.Manifest(**GOOD_MANIFEST).sha256 == "a" * 64
    assert manifest.Manifest(**(GOOD_MANIFEST | {"sha256": "", "source_date": ""})).rows == 58


@pytest.mark.parametrize(
    "spoil", [{"rows": 0}, {"sha256": "a" * 63}, {"extra": 1}], ids=["rows-0", "sha-63", "extra"]
)
def test_a308_a_manifest_that_does_not_read_is_refused(check: Run, spoil: dict[str, Any]) -> None:
    """A308: `--manifest` не того вида — отказ словами, файл не читается."""
    assert check(SAMPLE_BYTES, **spoil) == (1, "", UNREADABLE + NL)


def test_a308_a_manifest_that_is_not_json_or_is_missing_is_refused(
    tmp_path: Path, monkeypatch: MP, capsys: Cap
) -> None:
    """A308: `--manifest` — не JSON или файла нет: тот же отказ словами."""
    quiet(monkeypatch)
    path, spec = files(tmp_path, SAMPLE_BYTES, {})
    Path(spec).write_text("{", encoding="utf-8")
    codes = [command.main(["check", path, "--manifest", where]) for where in (spec, path + "x")]
    assert (codes, capsys.readouterr()) == ([1, 1], ("", (UNREADABLE + NL) * 2))


def test_a303_the_manifest_of_the_package_has_its_form_and_is_not_the_fixture() -> None:
    """A303: манифест пакета — формат 1, строк 58, хеш и дата — по форме или пустые вместе (ветвь
    печатается, как A15); sha256 Ф — не хеш манифеста, коды Ф и Ф2 — 90–99: фикстура не та."""
    found = manifest.MANIFEST
    assert (found.format, found.rows) == (1, 58)
    if found.sha256:
        print("ветвь: манифест файла ведущей")
        assert re.fullmatch("[0-9a-f]{64}", found.sha256)
        assert date.fromisoformat(found.source_date)
    else:
        print("ветвь: до файла ведущей — хеш и дата пустые")
        assert found.source_date == ""
    assert hashlib.sha256(SAMPLE_BYTES).hexdigest() != found.sha256
    heads = {int(item["code"].split(".")[0]) for item in SAMPLE + SAMPLE2}
    assert heads <= set(range(90, 100))
