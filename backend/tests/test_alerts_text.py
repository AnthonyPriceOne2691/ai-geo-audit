"""Текст тревоги (A241): шапка, время UTC на `Z`, строки тела, совет; длина — в единицах UTF-16,
как у Telegram; обрезка с «…» и «…ещё N строк»; секрет настроек — маской вычистки журнала."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta, timezone

import pytest

from ai_geo_audit import alerts, config
from ai_geo_audit.logs import setup as log_setup

NOW = datetime(2026, 11, 25, 13, 5, 7, 450_000, tzinfo=UTC)
TIME = "Время: 2026-11-25T13:05:07Z"
#: Знак вне основной плоскости: в UTF-16 — две единицы.
EMOJI = "\N{GRINNING FACE}"
#: Секреты настроек, которых не должно быть в тексте: пароль в адресе Redis и подпись пропусков.
LABEL = "alert-text-label-123456"
SIGNATURE = "alert-sign-label-123456"


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


@pytest.mark.parametrize("extra", [0, 1], ids=["exact", "over"])
def test_a241_part_exactly_at_its_limit_is_whole(extra: int) -> None:
    """A241: заголовок ровно 200, совет ровно 500 и строка ровно 1 000 единиц целы; единицей
    длиннее — обрезаны с «…»."""
    parts = [(200, "з"), (1000, "с"), (500, "д")]
    title, line, todo = [char * (limit + extra) for limit, char in parts]
    expected = [char * (limit - extra) + "…" * extra for limit, char in parts]
    head, _time, body, tail = alerts.compose("СБОЙ", title, [line], todo, NOW).split("\n")
    assert [head, body, tail] == [
        f"[ai-geo-audit · dev] СБОЙ: {expected[0]}",
        expected[1],
        f"Что делать: {expected[2]}",
    ]


@pytest.mark.parametrize("extra", [0, 1], ids=["exact", "over"])
def test_a241_text_exactly_at_the_limit_keeps_its_lines(extra: int) -> None:
    """A241: текст ровно 4 096 единиц цел; единицей длиннее — последней строки тела нет."""
    todo, whole = "д" * 100, ["ж" * 1000] * 3
    rest = alerts.MAX_UNITS - units(alerts.compose("СБОЙ", "x", [*whole, ""], todo, NOW))
    text = alerts.compose("СБОЙ", "x", [*whole, "ж" * (rest + extra)], todo, NOW)
    last = "…ещё 1 строка" if extra else "ж" * rest
    assert text.split("\n")[2:] == [*whole, last, f"Что делать: {todo}"]
    assert extra or units(text) == alerts.MAX_UNITS == 4096


def test_a241_lone_surrogate_is_replaced_and_the_rest_is_whole() -> None:
    """A241: одинокий суррогат в части — имя файла не в UTF-8, прочитанное с `surrogateescape`, — в
    тексте знак замены: тревога из-за одного знака не пропадает; пара суррогатов — её знак."""
    broken, pair = chr(0xDC80), chr(0xD83D) + chr(0xDE00)
    text = alerts.compose("СБОЙ", f"t{broken}", [f"файл {broken}.csv", pair], f"d{broken}", NOW)
    whole = chr(0xFFFD)
    assert text.split("\n") == [
        f"[ai-geo-audit · dev] СБОЙ: t{whole}",
        TIME,
        f"файл {whole}.csv",
        EMOJI,
        f"Что делать: d{whole}",
    ]


def test_a241_time_is_utc_in_any_process_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    """A241: время — UTC и тогда, когда пояс процесса другой: в CI он UTC и порчу пояса не
    видит."""
    monkeypatch.setenv("TZ", "JST-9")
    time.tzset()
    try:
        assert alerts.stamp(NOW) == "2026-11-25T13:05:07Z"
    finally:
        monkeypatch.undo()
        time.tzset()


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
    """A241, В7: значение секрета настроек — пароль в адресе хранилища, подпись пропусков — в строке
    тела, заголовке и совете — маска вычистки журнала; в тексте его нет."""
    monkeypatch.setenv("REDIS_URL", "redis://:" + LABEL + "@127.0.0.1:6462/0")
    monkeypatch.setenv("JWT_SECRET", SIGNATURE)
    monkeypatch.setattr(config, "storage", config.StorageSettings())
    monkeypatch.setattr(config, "access", config.AccessSettings())
    lines = [f"пароль {LABEL} в строке", f"подпись {SIGNATURE} в строке"]
    text = alerts.compose("СБОЙ", f"t {LABEL}", lines, f"d {LABEL}", NOW)
    assert LABEL not in text and SIGNATURE not in text
    assert text.count(log_setup.MASK) == 4


#: Токен бота тревог (A192): свой у теста — маску даёт только отметка домена тревог в `compose`.
BOT_TOKEN = "123456789:alert-bot-token-value-0003"


def test_a192_bot_token_in_an_alert_line_becomes_the_mask(monkeypatch: pytest.MonkeyPatch) -> None:
    """A192, находка 1 третьей проверяющей: тревога из процесса, где журнал не настроен, со строкой
    «POST …/bot<токен>/sendMessage — 502» — токена в тексте нет, на его месте маска: `compose`
    сама отмечает домен тревог. Порча «домен тревог в `compose` не отмечен» — токен в тексте
    тревоги, дальше в исходящих Redis и в Telegram."""
    monkeypatch.setenv("ALERT_TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setattr(config, "alerts", config.AlertSettings())
    line = f"POST https://api.telegram.example/bot{BOT_TOKEN}/sendMessage — 502"
    text = alerts.compose("СБОЙ", "доставка", [line], "проверить бота", NOW)
    assert BOT_TOKEN not in text
    assert f"/bot{log_setup.MASK}/sendMessage — 502" in text
