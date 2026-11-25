"""Текст тревоги (A241): шапка, время UTC на `Z`, строки тела, совет; длина — в единицах UTF-16,
как у Telegram; обрезка с «…» и «…ещё N строк»; секрет настроек — маской вычистки журнала."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from ai_geo_audit import alerts, config
from ai_geo_audit.logs import setup as log_setup

NOW = datetime(2026, 11, 25, 13, 5, 7, 450_000, tzinfo=UTC)
TIME = "Время: 2026-11-25T13:05:07Z"
#: Знак вне основной плоскости: в UTF-16 — две единицы.
EMOJI = "\N{GRINNING FACE}"
#: Секрет настроек, которого не должно быть в тексте: пароль в адресе Redis.
LABEL = "alert-text-label-123456"


def units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def mode(monkeypatch: pytest.MonkeyPatch, env: str) -> None:
    monkeypatch.setenv("APP_ENV", env)
    monkeypatch.setattr(config, "app", config.AppSettings())


def test_a241_text_is_header_time_body_and_todo(monkeypatch: pytest.MonkeyPatch) -> None:
    """A241: ВНИМАНИЕ, три строки, совет; в бою имя без режима, вне боя — с режимом; время —
    UTC до секунды на `Z`, в любом поясе часов; разметки нет."""
    mode(monkeypatch, "prod")
    lines = ["первая", "вторая <b>как есть</b>", "третья *как есть*"]
    text = alerts.compose("ВНИМАНИЕ", "очередь стоит", lines, "проверить воркер", NOW)
    assert text.split("\n") == [
        "[ai-geo-audit] ВНИМАНИЕ: очередь стоит",
        TIME,
        *lines,
        "Что делать: проверить воркер",
    ]
    mode(monkeypatch, "dev")
    tokyo = NOW.astimezone(timezone(timedelta(hours=9)))
    head, time = alerts.compose("СБОЙ", "x", [], "y", tokyo).split("\n")[:2]
    assert (head, time) == ("[ai-geo-audit · dev] СБОЙ: x", TIME)


@pytest.mark.parametrize(
    ("count", "size", "kept", "marker"),
    [
        (60, 100, 39, "…ещё 21 строка"),
        (40, 100, 39, "…ещё 1 строка"),
        (45, 90, 43, "…ещё 2 строки"),
        (44, 100, 39, "…ещё 5 строк"),
    ],
    ids=["body-6000", "one-line", "two-lines", "five-lines"],
)
def test_a241_long_body_loses_lines_from_the_end(
    count: int, size: int, kept: int, marker: str
) -> None:
    """A241: тело длиннее 4 096 единиц — строки тела режутся с конца, на их месте «…ещё N строк» в
    форме по числу; первая и последняя строки целы, текст в пределе."""
    body = [f"{n:02d}" + "ж" * (size - 2) for n in range(count)]
    text = alerts.compose("ВНИМАНИЕ", "длинное тело", body, "ничего", NOW)
    assert text.split("\n") == [
        "[ai-geo-audit · dev] ВНИМАНИЕ: длинное тело",
        TIME,
        *body[:kept],
        marker,
        "Что делать: ничего",
    ]
    assert units(text) <= alerts.MAX_UNITS


def test_a241_long_parts_are_cut_before_counting() -> None:
    """A241: строка тела длиннее 1 000, заголовок длиннее 200 и совет длиннее 500 единиц обрезаны
    с «…»; эмодзи — по две единицы, пара суррогатов не рвётся."""
    text = alerts.compose("СБОЙ", "з" * 300, ["с" * 5000, EMOJI * 2100], "д" * 600, NOW)
    head, _time, line, emoji, todo = text.split("\n")
    assert head == "[ai-geo-audit · dev] СБОЙ: " + "з" * 199 + "…"
    assert line == "с" * 999 + "…"
    assert emoji == EMOJI * 499 + "…"
    assert (units(emoji), units(line)) == (999, 1000)
    assert todo == "Что делать: " + "д" * 499 + "…"
    assert units(text) <= alerts.MAX_UNITS


@pytest.mark.parametrize(
    ("count", "words"),
    [
        (1, "1 строка"),
        (2, "2 строки"),
        (4, "4 строки"),
        (5, "5 строк"),
        (11, "11 строк"),
        (12, "12 строк"),
        (14, "14 строк"),
        (21, "21 строка"),
        (22, "22 строки"),
        (25, "25 строк"),
        (111, "111 строк"),
        (112, "112 строк"),
        (101, "101 строка"),
    ],
    ids=lambda value: str(value) if isinstance(value, int) else None,
)
def test_a241_more_lines_takes_the_form_by_number(count: int, words: str) -> None:
    """A241: форма слова по числу — 1 строка, 2 строки, 5 строк; 11–14 — «строк»."""
    assert alerts.more_lines(count) == f"…ещё {words}"


def test_a241_setting_secret_in_a_line_becomes_the_mask(monkeypatch: pytest.MonkeyPatch) -> None:
    """A241, В7: значение секрета настроек в строке тела, заголовке и совете — маска вычистки
    журнала; в тексте его нет."""
    monkeypatch.setenv("REDIS_URL", "redis://:" + LABEL + "@127.0.0.1:6462/0")
    monkeypatch.setattr(config, "storage", config.StorageSettings())
    text = alerts.compose("СБОЙ", f"t {LABEL}", [f"пароль {LABEL} в строке"], f"d {LABEL}", NOW)
    assert LABEL not in text
    assert text.count(log_setup.MASK) == 3
