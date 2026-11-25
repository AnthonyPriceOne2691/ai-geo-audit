"""Мастер-ключ и шифр ключей сервисов (A220, A222, A223, A224, A228, A237): разбор файла, шифр с
именем внутри, номер мастер-ключа, отпечаток и подсказка; метки нет ни в отказе, ни в журнале."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
from pathlib import Path

import leaks
import masterkeys
import pytest
from cryptography.fernet import Fernet, InvalidToken

from ai_geo_audit.config import master_key
from ai_geo_audit.logs import setup as logs
from ai_geo_audit.settings import crypto

use_file = masterkeys.use


def unreadable(name: str, ciphertext: str, master_id: str) -> crypto.KeyUnreadable:
    """Отказ расшифровки; поднят вне `except` — цепочки нет (A237)."""
    with pytest.raises(crypto.KeyUnreadable) as refused:
        crypto.unseal(name, ciphertext, master_id)
    assert leaks.links(refused.value) == 0
    return refused.value


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_a220_good_files_give_their_keys_in_order(newline: str) -> None:
    """A220: одна строка из 64 знаков hex — ключ 1; две разные строки и перевод строки в конце —
    ключей 2, по порядку строк, с номерами строк."""
    first, second = masterkeys.line(), masterkeys.line()
    one = master_key.parse(first.encode())
    two = master_key.parse(f"{first}{newline}{second}{newline}".encode())
    assert [key.raw for key in one] == [bytes.fromhex(first)]
    assert [(key.line, key.raw) for key in two] == [
        (1, bytes.fromhex(first)),
        (2, bytes.fromhex(second)),
    ]
    assert repr(two[0]) == "MasterKey(line=1)"


def test_a220_upper_case_with_crlf_is_the_same_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A220: строка заглавными hex с переводами строк CRLF — ключ 1, тот же, что строчными: те же
    байты, тот же номер мастер-ключа, и запись под одним файлом читается под другим."""
    row = masterkeys.line()
    lower = masterkeys.write(tmp_path / "lower.key", [row])
    upper = masterkeys.write(tmp_path / "upper.key", [row.upper()], newline="\r\n")
    assert [key.raw for key in master_key.read(str(upper))] == [bytes.fromhex(row)]
    value = masterkeys.marker()
    use_file(monkeypatch, lower)
    sealed = crypto.seal("openai", value)
    use_file(monkeypatch, upper)
    assert crypto.seal("openai", value).master_id == sealed.master_id
    assert crypto.unseal("openai", sealed.ciphertext, sealed.master_id) == value


@pytest.mark.parametrize("case", list(masterkeys.BAD_FILES))
def test_a220_bad_file_is_refused_with_line_and_reason(tmp_path: Path, case: str) -> None:
    """A220, A237: негодный файл — `MasterKeyError`: имя `KEYS_MASTER_KEY_FILE`, номер строки и
    причина словами. Содержимого нет ни в тексте, ни в цепочке: отказ поднят вне `except`."""
    row = masterkeys.line()
    reason, content = masterkeys.bad_file(case, row)
    path = tmp_path / "master.key"
    path.write_bytes(content)
    with pytest.raises(master_key.MasterKeyError) as refused:
        master_key.read(str(path))
    assert refused.value.args == (f"KEYS_MASTER_KEY_FILE: {reason}",)
    assert refused.value.reason == reason
    assert leaks.links(refused.value) == 0
    found = leaks.traces(refused.value)
    assert [part for part in (row, row.upper(), row[2:34], row[34:]) if part in found] == []


def test_a220_unset_folder_and_closed_file_are_refused_in_words(tmp_path: Path) -> None:
    """A220, соседние случаи: путь не задан, каталог вместо файла, файл без прав на чтение —
    отказ словами, без текста ошибки ОС и без цепочки."""
    folder = tmp_path / "folder"
    folder.mkdir()
    closed = masterkeys.write(tmp_path / "closed.key", [masterkeys.line()])
    closed.chmod(0)
    cases = {
        "": "не задано",
        str(folder): f"файл не читается: {folder}",
        str(closed): f"файл не читается: {closed}",
    }
    for path, reason in cases.items():
        with pytest.raises(master_key.MasterKeyError) as refused:
            master_key.read(path)
        assert refused.value.args == (f"KEYS_MASTER_KEY_FILE: {reason}",)
        assert leaks.links(refused.value) == 0


def test_a220_file_is_read_once_per_process(master_file: masterkeys.MasterFile) -> None:
    """In scope: файл читается один раз за процесс — после удаления файла шифр работает прежними
    ключами; новый процесс (сброс кэша) без файла — отказ."""
    value = masterkeys.marker()
    sealed = crypto.seal("openai", value)
    master_file.path.unlink()
    assert crypto.unseal("openai", sealed.ciphertext, sealed.master_id) == value
    master_key.read.cache_clear()
    with pytest.raises(master_key.MasterKeyError) as refused:
        crypto.seal("openai", value)
    assert refused.value.reason == f"файл не читается: {master_file.path}"


def test_a222_two_ciphers_differ_and_hide_the_value(master_file: masterkeys.MasterFile) -> None:
    """A222: два шифра одного значения разные; метки нет ни в одном — ни открыто, ни в байтах после
    раскодирования base64; своим именем шифр открывается — метка."""
    value = masterkeys.marker()
    first, second = crypto.seal("openai", value), crypto.seal("openai", value)
    assert first.ciphertext != second.ciphertext
    for sealed in (first, second):
        assert value not in sealed.ciphertext
        assert value.encode() not in base64.urlsafe_b64decode(sealed.ciphertext)
        assert crypto.unseal("openai", sealed.ciphertext, sealed.master_id) == value


def test_a222_foreign_name_and_changed_sign_are_refused(master_file: masterkeys.MasterFile) -> None:
    """A222, A237: тот же шифротекст под именем `ahrefs` — «шифротекст другого ключа: Ahrefs»;
    шифротекст с одним изменённым знаком — «не расшифровывается»; метки нет в отказах."""
    value = masterkeys.marker()
    sealed = crypto.seal("openai", value)
    middle = len(sealed.ciphertext) // 2
    swap = "A" if sealed.ciphertext[middle] != "A" else "B"
    changed = f"{sealed.ciphertext[:middle]}{swap}{sealed.ciphertext[middle + 1 :]}"
    cases = [
        ("ahrefs", sealed.ciphertext, "шифротекст другого ключа: Ahrefs"),
        ("openai", changed, "не расшифровывается: OpenAI"),
        ("openai", f"{sealed.ciphertext[:-1]}ж", "не расшифровывается: OpenAI"),
    ]
    for name, ciphertext, text in cases:
        refusal = unreadable(name, ciphertext, sealed.master_id)
        assert refusal.args == (text,)
        assert value not in leaks.traces(refusal)


@pytest.mark.parametrize(
    "inside",
    [
        "{value} — не JSON",
        '["openai", "{value}"]',
        '{{"name": "openai", "valu": "{value}"}}',
        '{{"name": "openai", "value": 7, "x": "{value}"}}',
        '{{"name": 7, "value": "{value}"}}',
    ],
    ids=["not-json", "list", "no-value", "value-not-text", "name-not-text"],
)
def test_a222_good_token_with_no_pair_inside_is_refused(
    master_file: masterkeys.MasterFile, inside: str
) -> None:
    """A222, A237, соседние случаи: токен годен под мастер-ключом, а внутри не пара «имя и
    значение» — «не расшифровывается», без цепочки: `.doc` ошибки JSON держал бы метку."""
    value = masterkeys.marker()
    master_id = crypto.seal("openai", value).master_id
    token = crypto._ring().cipher.encrypt(inside.format(value=value).encode()).decode()
    refusal = unreadable("openai", token, master_id)
    assert refusal.args == ("не расшифровывается: OpenAI",)
    assert value not in leaks.traces(refusal)


def test_a222_token_with_broken_utf8_inside_is_refused(master_file: masterkeys.MasterFile) -> None:
    """A222, соседний случай: внутри токена байты не UTF-8 — «не расшифровывается», без цепочки."""
    master_id = crypto.seal("openai", masterkeys.marker()).master_id
    token = crypto._ring().cipher.encrypt(b'{"name": "openai", "value": "\xff"}').decode()
    assert unreadable("openai", token, master_id).args == ("не расшифровывается: OpenAI",)


def test_a223_previous_line_opens_and_new_records_use_the_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A223, часть без базы: запись сделана, когда в файле была только вторая строка, — под файлом
    из двух строк она расшифровывается; у новой записи номер мастер-ключа — от первой строки. Без
    второй строки — «ключ OpenAI зашифрован мастер-ключом с номером N, его нет в файле»."""
    first, second = masterkeys.line(), masterkeys.line()
    path = tmp_path / "master.key"
    value = masterkeys.marker()
    use_file(monkeypatch, masterkeys.write(path, [second]))
    old = crypto.seal("openai", value)
    use_file(monkeypatch, masterkeys.write(path, [first, second]))
    assert crypto.unseal("openai", old.ciphertext, old.master_id) == value
    new = crypto.seal("openai", value)
    use_file(monkeypatch, masterkeys.write(path, [first]))
    assert crypto.seal("openai", value).master_id == new.master_id != old.master_id
    assert crypto.unseal("openai", new.ciphertext, new.master_id) == value
    refusal = unreadable("openai", old.ciphertext, old.master_id)
    assert refusal.args == (
        f"ключ OpenAI зашифрован мастер-ключом с номером {old.master_id}, его нет в файле",
    )
    assert value not in leaks.traces(refusal)


def test_a224_master_id_and_fingerprint_are_hex_of_fixed_length(
    master_file: masterkeys.MasterFile,
) -> None:
    """A224: у записи номер мастер-ключа — 8 знаков hex, отпечаток — 12; в `repr` записи нет ни
    шифротекста, ни подсказки."""
    value = masterkeys.marker()
    sealed = crypto.seal("openai", value)
    assert re.fullmatch(r"[0-9a-f]{8}", sealed.master_id)
    assert re.fullmatch(r"[0-9a-f]{12}", sealed.fingerprint)
    assert (
        repr(sealed)
        == f"Sealed(master_id='{sealed.master_id}', fingerprint='{sealed.fingerprint}')"
    )


def test_a224_fingerprint_is_keyed_by_name_value_and_current_master(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A224: отпечаток — HMAC имени и значения на ключе текущего мастер-ключа, то есть первой строки
    файла: то же значение — тот же отпечаток, и под файлом из одной первой строки тоже; другое
    значение, другое имя или другая первая строка — другой."""
    first, second = masterkeys.line(), masterkeys.line()
    path = tmp_path / "master.key"
    value = masterkeys.marker()
    use_file(monkeypatch, masterkeys.write(path, [first, second]))
    same = crypto.seal("openai", value).fingerprint
    assert crypto.seal("openai", value).fingerprint == same
    assert crypto.seal("openai", f"{value}x").fingerprint != same
    assert crypto.seal("ahrefs", value).fingerprint != same
    use_file(monkeypatch, masterkeys.write(path, [first]))
    assert crypto.seal("openai", value).fingerprint == same
    use_file(monkeypatch, masterkeys.write(path, [second, first]))
    assert crypto.seal("openai", value).fingerprint != same


#: Строка-образец файла и её известные ответы для пары «openai» и `SAMPLE_VALUE` — формат записей
#: в базе: смена метки назначения или вывода ключей делает нечитаемыми все записи и краснеет здесь.
#: Ответы посчитаны прямым HMAC-SHA256, мимо `settings.crypto` (находка 1 проверяющей, срез 1).
SAMPLE = bytes(range(32)).hex()
SAMPLE_VALUE = "metka-0123456789"
SAMPLE_MASTER_ID = "02533a8e"
SAMPLE_FINGERPRINT = "f795de2554f2"


def fernet_key(row: str) -> bytes:
    """Ключ Fernet строки файла, как его выводит шифр: HMAC-SHA256 с меткой назначения."""
    derived = hmac.new(bytes.fromhex(row), b"ai-geo-audit fernet v1", hashlib.sha256).digest()
    return base64.urlsafe_b64encode(derived)


def test_a224_known_answers_pin_the_record_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A222, A224, решение среза 0: на строке-образце номер мастер-ключа и отпечаток — известные
    константы; шифротекст открывает Fernet от HMAC строки с меткой назначения, а Fernet от самой
    строки — нет."""
    use_file(monkeypatch, masterkeys.write(tmp_path / "master.key", [SAMPLE]))
    sealed = crypto.seal("openai", SAMPLE_VALUE)
    assert (sealed.master_id, sealed.fingerprint) == (SAMPLE_MASTER_ID, SAMPLE_FINGERPRINT)
    token = sealed.ciphertext.encode()
    pair = json.loads(Fernet(fernet_key(SAMPLE)).decrypt(token))
    assert pair == {"name": "openai", "value": SAMPLE_VALUE}
    with pytest.raises(InvalidToken):
        Fernet(base64.urlsafe_b64encode(bytes.fromhex(SAMPLE))).decrypt(token)


@pytest.mark.parametrize(("length", "hint"), [(19, None), (20, "wxyz"), (51, "wxyz")])
def test_a224_hint_is_four_last_signs_of_a_long_value(length: int, hint: str | None) -> None:
    """A224: подсказка — последние 4 знака значения длиной от 20 знаков; у короткого её нет."""
    value = "a" * (length - 4) + "wxyz"
    assert crypto.hint(value) == hint


def mixed(row: str) -> str:
    """Строка файла в смешанном регистре: её закрывает только форма «как записана»."""
    return "".join(sign.upper() if place % 2 else sign for place, sign in enumerate(row))


#: Форма секрета → строка файла, как записана, и что уходит в журнал (находка 3 проверяющей).
SECRET_FORMS = {
    "value": (lambda row: row, lambda row, value: value),
    "upper-of-lower-file": (lambda row: row, lambda row, value: row.upper()),
    "lower-of-upper-file": (lambda row: row.upper(), lambda row, value: row),
    "mixed-as-written": (mixed, lambda row, value: mixed(row)),
    "fernet-key": (lambda row: row, lambda row, value: fernet_key(row).decode()),
}


@pytest.mark.parametrize("fmt", ["json", "text"])
@pytest.mark.parametrize("form", list(SECRET_FORMS))
def test_a228_every_secret_form_is_masked_in_the_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    form: str,
    fmt: str,
) -> None:
    """A228: после первого чтения файла мастер-ключа и записи ключа запись журнала с секретом в
    тексте и в поле `extra` выходит с маской `***`: значение после `seal`; строка файла в другом
    регистре, чем записана, и как записана; выведенный ключ Fernet."""
    row, value = masterkeys.line(), masterkeys.marker()
    written, logged = SECRET_FORMS[form]
    use_file(monkeypatch, masterkeys.write(tmp_path / "k", [written(row)], newline="\r\n"))
    crypto.seal("openai", value)
    secret = logged(row, value)
    logs.setup_logging(level="INFO", fmt=fmt)
    logging.getLogger("ai_geo_audit.test").info("секрет %s", secret, extra={"secret": secret})
    out = capsys.readouterr().err
    assert secret not in out
    assert out.count(logs.MASK) >= 2
    if fmt == "json":
        assert json.loads(out.splitlines()[-1])["secret"] == logs.MASK
