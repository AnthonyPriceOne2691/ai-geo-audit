"""Постановка в исходящие (A240): запись по форме — в начало списка, предел 1 000 со счётчиком
отброшенных; срок 2 с и вид сбоя словами; вызывающий исключений не видит; повторов клиента нет."""

from __future__ import annotations

import json
import re
import socket
import time

import pytest
import redis
import redis_fakes
import redistools

from ai_geo_audit import alerts, config
from ai_geo_audit.logs import setup as log_setup

TITLE = "проверка канала"
#: Ответы подставного Redis на круг `WATCH`–`EXEC`, где первый `EXEC` снят чужой правкой списка:
#: в RESP3 пустой ответ — `_`, а не `*-1` RESP2 (клиент `redis` 8 говорит RESP3, decisions, срез 0).
CONFLICT = {
    **redis_fakes.FULL,
    b"WATCH": b"+OK\r\n",
    b"UNWATCH": b"+OK\r\n",
    b"LLEN": b":0\r\n",
    b"MULTI": b"+OK\r\n",
    b"LPUSH": b"+QUEUED\r\n",
    b"LTRIM": b"+QUEUED\r\n",
    b"EXEC": [b"_\r\n", b"*2\r\n:1\r\n+OK\r\n"],
}


def use(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    monkeypatch.setenv("REDIS_URL", url)
    monkeypatch.setattr(config, "storage", config.StorageSettings())


@pytest.fixture
def outbox(redis_url: str, monkeypatch: pytest.MonkeyPatch) -> redis.Redis:
    """Redis теста для кода под тестом. Журнал тест настраивает сам, в теле: обработчик из фикстуры
    до вызова теста не доживает."""
    use(monkeypatch, redis_url)
    return redistools.client(redistools.session_db())


def journal(err: str, level: str) -> list[str]:
    """Сообщения записей журнала уровня `level` из потока ошибок."""
    lines = [json.loads(line) for line in err.splitlines() if line.startswith("{")]
    return [str(record["message"]) for record in lines if record["level"] == level]


def closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def test_a240_alert_lands_first_as_one_ascii_json_line(outbox: redis.Redis) -> None:
    """A240: запись — JSON только из ASCII одной строкой, ровно `id`, `ts`, `text`; `id` — 32 знака
    hex, `ts` — UTC на `Z`; новая — в начале списка; секрет настроек в строке — маска (A241)."""
    line = f"ёлка \N{GRINNING FACE} и {redistools.PASSWORD}"
    assert alerts.queue_alert("ПРОВЕРКА", TITLE, [line], "ничего") is True
    assert alerts.queue_alert("ВНИМАНИЕ", "вторая", [], "ничего") is True
    raw = outbox.lindex(alerts.OUTBOX, 1)
    assert raw is not None and raw.isascii() and b"\n" not in raw
    record = json.loads(raw)
    assert set(record) == {"id", "ts", "text"}
    assert re.fullmatch(r"[0-9a-f]{32}", record["id"])
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", record["ts"])
    first, time_line, body, _todo = record["text"].split("\n")
    assert (first, time_line) == (
        f"[ai-geo-audit · dev] ПРОВЕРКА: {TITLE}",
        f"Время: {record['ts']}",
    )
    assert redistools.PASSWORD not in body and log_setup.MASK in body and "ёлка" in body
    assert json.loads(outbox.lindex(alerts.OUTBOX, 0) or b"{}")["text"].startswith(
        "[ai-geo-audit · dev] ВНИМАНИЕ: вторая"
    )


def test_a240_full_outbox_keeps_1000_and_counts_the_dropped(
    outbox: redis.Redis, capsys: pytest.CaptureFixture[str]
) -> None:
    """A240: в исходящих 999 — тревога встаёт тысячной без отброса; следующая отбрасывает самую
    старую: в списке 1 000, счётчик отброшенных — 1, строка журнала «исходящие полны — отброшено
    1»."""
    log_setup.setup_logging(fmt="json")
    outbox.rpush(alerts.OUTBOX, *[f"old-{n}" for n in range(999)])
    assert alerts.queue_alert("ВНИМАНИЕ", "тысячная", [], "ничего") is True
    assert (outbox.llen(alerts.OUTBOX), outbox.get(alerts.DROPPED)) == (1000, None)
    assert journal(capsys.readouterr().err, "WARNING") == []
    assert alerts.queue_alert("ВНИМАНИЕ", "сверх", [], "ничего") is True
    assert (outbox.llen(alerts.OUTBOX), outbox.get(alerts.DROPPED)) == (1000, b"1")
    assert outbox.lindex(alerts.OUTBOX, -1) == b"old-997"
    assert "сверх" in json.loads(outbox.lindex(alerts.OUTBOX, 0) or b"{}")["text"]
    assert journal(capsys.readouterr().err, "WARNING") == ["исходящие полны — отброшено 1"]


def test_a240_longest_record_fits_32_kib(outbox: redis.Redis) -> None:
    """A240: самая длинная запись — 4 096 единиц не-ASCII записью `\\uXXXX` — до 32 КиБ."""
    assert alerts.queue_alert("СБОЙ", "ж" * 300, ["ж" * 1000] * 6, "ж" * 600) is True
    raw = outbox.lindex(alerts.OUTBOX, 0) or b""
    assert raw.isascii() and len(raw) <= 32 * 1024


@pytest.mark.parametrize(
    ("case", "kind"),
    [
        ("closed", "Redis не принимает соединение"),
        ("oom", "Redis полон: запись отвергнута"),
        ("noauth", "Redis не пустил: нужен верный пароль в адресе"),
    ],
)
def test_a240_refusals_are_one_error_line_in_words_and_false(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], case: str, kind: str
) -> None:
    """A240: закрытый порт, полный Redis (`OOM`, `noeviction`) и Redis без входа (`NOAUTH`) — итог
    `False`, одна строка ERROR «ТРЕВОГА НЕ ПОСТАВЛЕНА» с видом сбоя словами, исключения нет; ни
    адреса, ни пароля в выводе."""
    log_setup.setup_logging(fmt="json")
    replies = {"oom": redis_fakes.OOM, "noauth": redis_fakes.NOAUTH}
    with redis_fakes.running(default=replies.get(case, redis_fakes.OOM)) as fake:
        port = closed_port() if case == "closed" else fake.port
        use(monkeypatch, redis_fakes.url(port))
        assert alerts.queue_alert("ПРОВЕРКА", TITLE, ["x"], "ничего") is False
    err = capsys.readouterr().err
    assert journal(err, "ERROR") == [f"ТРЕВОГА НЕ ПОСТАВЛЕНА — {kind}: ПРОВЕРКА «{TITLE}»"]
    assert "127.0.0.1" not in err and f":{port}" not in err and redis_fakes.PASSWORD not in err


def test_a240_silent_redis_is_cut_at_two_seconds_once(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A240: Redis принял соединение и молчит — `False` и строка ERROR не позже 2,5 с; соединение
    одно: повторов у клиента нет (decisions, срез 0)."""
    log_setup.setup_logging(fmt="json")
    with redis_fakes.running(silent=True) as fake:
        use(monkeypatch, fake.url)
        start = time.monotonic()
        assert alerts.queue_alert("ПРОВЕРКА", TITLE, [], "ничего") is False
        elapsed = time.monotonic() - start
        accepted = fake.accepted
    assert alerts.DEADLINE_SECONDS <= elapsed <= 2.5
    assert accepted == 1
    expected = f"ТРЕВОГА НЕ ПОСТАВЛЕНА — Redis не ответил за 2 с: ПРОВЕРКА «{TITLE}»"
    assert journal(capsys.readouterr().err, "ERROR") == [expected]


def test_a240_watch_conflict_is_retried_and_queued_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """A240, вопрос 5: чужая правка списка между `LLEN` и `EXEC` снимает транзакцию (`WatchError`) —
    постановка идёт кругом заново и кладёт запись один раз."""
    with redis_fakes.running(replies=CONFLICT, default=b"-ERR unexpected\r\n") as fake:
        use(monkeypatch, fake.url)
        assert alerts.queue_alert("ПРОВЕРКА", TITLE, [], "ничего") is True
        commands = list(fake.commands)
    assert (commands.count(b"EXEC"), commands.count(b"WATCH")) == (2, 2)


async def test_a240_call_from_a_running_loop_is_false_in_words(
    outbox: redis.Redis, capsys: pytest.CaptureFixture[str]
) -> None:
    """A240: синхронный вызов из работающего цикла событий — `False` и строка ERROR словами, без
    «coroutine was never awaited»; асинхронный вариант — Ч1.14б."""
    log_setup.setup_logging(fmt="json")
    assert alerts.queue_alert("ПРОВЕРКА", TITLE, [], "ничего") is False
    err = capsys.readouterr().err
    kind = "вызов из работающего цикла событий: нужен асинхронный вариант (Ч1.14б)"
    assert journal(err, "ERROR") == [f"ТРЕВОГА НЕ ПОСТАВЛЕНА — {kind}: ПРОВЕРКА «{TITLE}»"]
    assert "never awaited" not in err and outbox.llen(alerts.OUTBOX) == 0
