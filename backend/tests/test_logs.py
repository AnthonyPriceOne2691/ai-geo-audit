"""Логи: поля и идентификаторы доживают до вывода, время в UTC, секретов в выводе нет."""

from __future__ import annotations

import json
import logging
import time

import pytest

from ai_geo_audit import config
from ai_geo_audit.logs import setup as logs
from ai_geo_audit.logs.setup import JsonFormatter, TextFormatter

# pragma: allowlist nextline secret
SECRET = "s3cr3t-token-value-1234567890"
# pragma: allowlist nextline secret
DB_PASSWORD = "db-password-123456"
DSN = f"postgresql+asyncpg://audit:{DB_PASSWORD}@db:5432/audit"


def lines(capsys: pytest.CaptureFixture[str]) -> list[str]:
    return [line for line in capsys.readouterr().err.splitlines() if line]


def test_a5_json_line_carries_ids_and_extra(capsys: pytest.CaptureFixture[str]) -> None:
    """A5: в JSON — id запроса и аудита и поля из `extra`, время с `+0000`."""
    logs.setup_logging(level="INFO", fmt="json")
    with logs.request_context("req-1"), logs.audit_context(47):
        logging.getLogger("ai_geo_audit.test").info("аудит начат", extra={"pages": 10})
    record = json.loads(lines(capsys)[-1])
    assert (record["request_id"], record["audit_id"], record["pages"]) == ("req-1", "47", 10)
    assert record["ts"].endswith("+0000")


def test_a5_text_line_keeps_ids_and_extra(capsys: pytest.CaptureFixture[str]) -> None:
    """A5: текстовая строка не теряет поля — они в хвосте."""
    logs.setup_logging(level="INFO", fmt="text")
    with logs.request_context("req-2"):
        logging.getLogger("ai_geo_audit.test").info("готово", extra={"pages": 3})
    line = lines(capsys)[-1]
    assert "request_id=req-2" in line and "pages=3" in line and "+0000" in line


def test_a5_time_is_utc_whatever_the_machine_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    """A5: пояс машины на время в записи не влияет."""
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    time.tzset()
    try:
        record = logging.LogRecord("x", logging.INFO, __file__, 1, "m", None, None)
        record.created, record.msecs = 0.0, 0.0
        epoch = "1970-01-01T00:00:00.000+0000"
        assert json.loads(JsonFormatter().format(record))["ts"] == epoch
        assert TextFormatter().format(record).startswith(epoch)
    finally:
        monkeypatch.undo()
        time.tzset()


@pytest.mark.parametrize("fmt", ["json", "text"])
def test_a6_secret_never_reaches_output(capsys: pytest.CaptureFixture[str], fmt: str) -> None:
    """A6: секрета нет ни в сообщении, ни в полях, ни в исключении, ни в журнале httpx."""
    logs.register_secret(SECRET)
    logs.setup_logging(level="INFO", fmt=fmt)
    log = logging.getLogger("ai_geo_audit.test")
    log.info("токен %s", SECRET, extra={"token": SECRET, "nested": {"key": SECRET}})
    try:
        raise RuntimeError(f"сбой с {SECRET}")
    except RuntimeError:
        log.exception("ошибка")
    logging.getLogger("httpx").warning("POST https://api.example.test/bot%s/send", SECRET)
    out = capsys.readouterr().err
    assert SECRET not in out
    assert out.count("***") >= 4


def test_a6_config_secrets_are_registered(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A6: секретные настройки и пароль из адреса базы вычищаются без ручной отметки."""
    monkeypatch.setenv("JWT_SECRET", SECRET)
    monkeypatch.setenv("DATABASE_URL", DSN)
    logs.register_config_secrets(config.AccessSettings(), config.StorageSettings())
    logs.setup_logging(fmt="text")
    # Запись — мимо метода логгера: страж исполнения ловит секрет в вызове лога (так и надо),
    # а тест вносит его нарочно, чтобы судить маску в обработчике.
    log = logging.getLogger("x")
    args = (DSN, SECRET, DB_PASSWORD)
    log.handle(
        log.makeRecord("x", logging.WARNING, __file__, 0, "dsn=%s secret=%s pw=%s", args, None)
    )
    out = capsys.readouterr().err
    assert SECRET not in out and DB_PASSWORD not in out


def test_a6_httpx_info_is_silenced() -> None:
    """A6: httpx и httpcore на INFO печатают адрес целиком — их уровень WARNING."""
    logs.setup_logging(level="DEBUG")
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING


def test_short_values_are_not_masked(capsys: pytest.CaptureFixture[str]) -> None:
    """A6: короткое значение не маскируется — иначе маска съела бы обычные слова журнала."""
    logs.register_secret("audit")
    logs.setup_logging(fmt="text")
    logging.getLogger("x").warning("audit started")
    assert "audit started" in lines(capsys)[-1]


def test_repeated_setup_does_not_duplicate_lines(capsys: pytest.CaptureFixture[str]) -> None:
    """A5: повторная настройка заменяет обработчик, а не добавляет второй."""
    logs.setup_logging(fmt="text")
    logs.setup_logging(fmt="text")
    logging.getLogger("x").warning("один раз")
    assert sum("один раз" in line for line in lines(capsys)) == 1


# pragma: allowlist nextline secret
REDIS_PASSWORD = "r3dis-pass-123456"
REDIS_URL = f"redis://:{REDIS_PASSWORD}@redis:6379/0"


@pytest.mark.parametrize("fmt", ["json", "text"])
def test_a21_redis_password_never_reaches_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], fmt: str
) -> None:
    """A21: `REDIS_URL` с паролем, оба формата журнала — пароля Redis в выводе нет."""
    monkeypatch.setenv("REDIS_URL", REDIS_URL)
    logs.register_config_secrets(config.StorageSettings())
    logs.setup_logging(fmt=fmt)
    logging.getLogger("x").warning("redis=%s pw=%s", REDIS_URL, REDIS_PASSWORD)
    assert REDIS_PASSWORD not in capsys.readouterr().err


def test_a21_redis_url_is_a_secret_in_repr_and_dump(monkeypatch: pytest.MonkeyPatch) -> None:
    """A21: `repr` настроек и `model_dump()` пароля Redis не показывают: адрес — `SecretStr`."""
    monkeypatch.setenv("REDIS_URL", REDIS_URL)
    settings = config.StorageSettings()
    assert REDIS_PASSWORD not in repr(settings)
    assert REDIS_PASSWORD not in str(settings.model_dump())


@pytest.mark.parametrize("fmt", ["json", "text"])
def test_a21_percent_encoded_db_password_is_scrubbed_in_both_forms(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], fmt: str
) -> None:
    """A21: пароль базы `p%40ss-word-123456` — в выводе нет ни записи, ни `p@ss-word-123456`."""
    # pragma: allowlist nextline secret
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://audit:p%40ss-word-123456@db:5432/a")
    logs.register_config_secrets(config.StorageSettings())
    logs.setup_logging(fmt=fmt)
    logging.getLogger("x").warning("pw=%s decoded=%s", "p%40ss-word-123456", "p@ss-word-123456")
    out = capsys.readouterr().err
    assert "p%40ss-word-123456" not in out and "p@ss-word-123456" not in out


@pytest.mark.parametrize("fmt", ["json", "text"])
def test_a21_regex_signs_in_passwords_are_scrubbed_literally(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], fmt: str
) -> None:
    """A21: пароль `p.ss+w(rd)*123456` в адресах базы и Redis вычищается буквально; запись
    журнала не падает."""
    # pragma: allowlist nextline secret
    password = "p.ss+w(rd)*123456"
    monkeypatch.setenv("DATABASE_URL", f"postgresql+asyncpg://audit:{password}@db:5432/a")
    monkeypatch.setenv("REDIS_URL", f"redis://:{password}@redis:6379/0")
    logs.register_config_secrets(config.StorageSettings())
    logs.setup_logging(fmt=fmt)
    logging.getLogger("x").warning("pw=%s and again %s", password, password)
    out = capsys.readouterr().err
    assert password not in out and "pw=***" in out


#: Строки DETAIL, пришедшие мимо движка: значение — и что после вычистки остаётся.
DETAILS = [
    ("Key (email)=(ivan\nsecret-mail) already exists.", "secret-mail", "Key (email)="),
    ("Failing row contains (7, secret). tail, x).", "secret", "Failing row contains"),
    ("Failing row contains (7, line\nsecret-two).", "secret-two", "Failing row contains"),
    (
        "Key (period)=([2026-01-01,secret-from)) conflicts with existing key "
        "(period)=([2026-01-15,secret-till)).",
        "secret-",
        "conflicts with existing key",
    ),
]


@pytest.mark.parametrize("fmt", ["json", "text"])
def test_a26_second_net_cuts_detail_values_that_bypass_the_engine(
    capsys: pytest.CaptureFixture[str], fmt: str
) -> None:
    """A26: строки DETAIL мимо движка — значение с переводом строки (`Key` и `Failing row`), с
    «).» внутри, исключающее ограничение — значения нет ни в одном случае, вид ошибки и имя
    ограничения остались; честный текст проходит без изменений. Число случаев — в выводе."""
    logs.setup_logging(fmt=fmt)
    for detail, value, kept in DETAILS:
        logging.getLogger("x").error('violates constraint "uq_probe_key"\nDETAIL: %s', detail)
        out = capsys.readouterr().err
        assert value not in out and kept in out and "uq_probe_key" in out, detail
    honest = 'duplicate key value violates unique constraint "uq_probe_key" (Key names only)'
    assert logs.redact(honest) == honest
    print(f"A26: проверено случаев DETAIL: {len(DETAILS)}")


# Импорт свойства — здесь, а не в шапке: строки значений выше записаны в `.secrets.baseline` своими
# номерами, и сдвиг заставил бы сканер секретов переснять снимок.
from hypothesis import given, settings
from hypothesis import strategies as st

#: Значение — от 6 знаков (короче `register_secret` не берёт) и без `*`: маска из него и состоит, и
#: значение со звёздочкой она собирает заново (`x***yzw` из `xabcdefyzw` при `abcdef`). Знаки
#: регулярных выражений — замена обязана быть буквальной.
VALUE = st.text("ab.+(\\$", min_size=6, max_size=12)
#: Текст между вхождениями: те же знаки, звёздочка и пробел; бывает пустым.
GAP = st.text("ab.+(\\$* ", max_size=4)


@st.composite
def values_in_text(draw: st.DrawFn) -> tuple[list[str], str]:
    """От одного до трёх значений; у каждого одно или несколько вхождений вперемешку, между
    вхождениями — свой текст."""
    values = draw(st.lists(VALUE, min_size=1, max_size=3, unique=True))
    copies: list[str] = []
    for value in values:
        copies += [value] * draw(st.integers(1, 3))
    text = draw(GAP)
    for copy in draw(st.permutations(copies)):
        text += copy + draw(GAP)
    return values, text


@settings(max_examples=200)
@given(case=values_in_text())
def test_a111_no_registered_value_survives_redact(case: tuple[list[str], str]) -> None:
    """A111: свойство над `redact` — от одного до трёх зарегистрированных значений, у каждого одно
    или несколько вхождений: в выводе нет ни одного. Мимо логгера: страж исполнения судит вызовы
    лога. Реестр на время примера — ровно эти значения, после — прежний."""
    values, text = case
    saved = set(logs._secrets)
    logs._secrets.clear()
    try:
        for value in values:
            logs.register_secret(value)
        out = logs.redact(text)
    finally:
        logs._secrets.clear()
        logs._secrets.update(saved)
    assert [value for value in values if value in out] == []


def test_a111_nested_value_is_masked_whole_longest_first() -> None:
    """A111, пример: пароль внутри адреса — адрес маскируется целиком, длинное значение первым.
    При обратном порядке в выводе остался бы кожух адреса вокруг маски пароля."""
    saved = set(logs._secrets)
    logs._secrets.clear()
    try:
        logs.register_secret(DB_PASSWORD)
        logs.register_secret(DSN)
        out = logs.redact(f"url={DSN} pw={DB_PASSWORD}")
    finally:
        logs._secrets.clear()
        logs._secrets.update(saved)
    assert out == f"url={logs.MASK} pw={logs.MASK}"
