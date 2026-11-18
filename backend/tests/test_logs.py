"""Логи: поля и идентификаторы доживают до вывода, время в UTC, секретов в выводе нет."""

from __future__ import annotations

import json
import logging
import time

import pytest

from ai_geo_audit import config
from ai_geo_audit.logs import setup as logs
from ai_geo_audit.logs.setup import JsonFormatter, TextFormatter

SECRET = "s3cr3t-token-value-1234567890"
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
    logging.getLogger("x").warning("dsn=%s secret=%s pw=%s", DSN, SECRET, DB_PASSWORD)
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
