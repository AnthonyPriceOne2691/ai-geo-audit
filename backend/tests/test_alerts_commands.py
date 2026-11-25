"""Команда `python -m ai_geo_audit.ops.alerts check` (A242): тревога ПРОВЕРКА в исходящие, код 0 или
1; журналы и крючки процесса — до работы; отказы словами — без трассировки, введённого значения и
секрета настроек.

Вызовы `main` в процессе держат порчи; подпроцессы — путь импорта и крючки процесса. Подпроцесс —
на исходном `src` и без переменных mutmut: под раскладкой `mutants/` батуты его уронили бы (L95).
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from collections.abc import Callable

import dbtools
import pytest
import redistools

from ai_geo_audit import alerts, config
from ai_geo_audit.ops import alerts as command

SRC = dbtools.BACKEND / "src"
#: Секрет настроек, которого не должно быть во всём выводе.
LABEL = "check-guard-label-123456"
#: Подпроцесс: после журналов и крючков — предупреждение Python и запись, которая не собирается,
#: обе с секретом настроек; затем сама команда.
NOISY = """
import logging, os, sys, warnings
from ai_geo_audit import alerts
from ai_geo_audit.ops import alerts as command
real = alerts.queue_alert
def noisy(*args, **kwargs):
    warnings.warn("предупреждение " + os.environ["JWT_SECRET"])
    logging.getLogger("probe").error("запись %s %s", os.environ["JWT_SECRET"])
    return real(*args, **kwargs)
alerts.queue_alert = noisy
sys.exit(command.main(["check"]))
"""


def use(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    monkeypatch.setenv("REDIS_URL", url)
    monkeypatch.setenv("LOG_FORMAT", "json")
    monkeypatch.setattr(config, "storage", config.StorageSettings())
    monkeypatch.setattr(config, "logs", config.LogSettings())


def traced(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Порядок шагов команды: журналы, крючки процесса, постановка. Крючки — заглушкой: настоящие
    остались бы на весь процесс pytest; их держит подпроцесс ниже."""
    calls: list[str] = []
    setup, queue = command.setup_logging, alerts.queue_alert

    def logs() -> None:
        calls.append("logs")
        setup()

    def queued(*args: object, **kwargs: object) -> bool:
        calls.append("queue")
        return queue(*args, **kwargs)  # type: ignore[arg-type]

    hooks: Callable[[], None] = lambda: calls.append("guard")  # noqa: E731
    monkeypatch.setattr(command, "setup_logging", logs)
    monkeypatch.setattr(command, "guard_process", hooks)
    monkeypatch.setattr(alerts, "queue_alert", queued)
    return calls


def process_env(**extra: str) -> dict[str, str]:
    """Окружение подпроцесса с нуля: исходный `src`, без `.env` и переменных разработчика;
    покрытию — `COVERAGE_*`."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONUTF8": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(SRC),
        "ENV_FILE": os.devnull,
        "LOG_FORMAT": "json",
    }
    env.update({k: v for k, v in os.environ.items() if k.startswith("COVERAGE_")})
    return env | extra


def run(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, *args], env=env, capture_output=True, text=True, timeout=60, check=False
    )


def test_a242_check_queues_one_alert_after_logs_and_hooks(
    redis_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A242: Redis теста отвечает — одна тревога ПРОВЕРКА в исходящих, вывод «поставлено в
    исходящие», код 0; журналы и крючки процесса — до постановки (L55)."""
    use(monkeypatch, redis_url)
    calls = traced(monkeypatch)
    assert command.main(["check"]) == 0
    assert capsys.readouterr().out == "поставлено в исходящие\n"
    assert calls == ["logs", "guard", "queue"]
    outbox = redistools.client(redistools.session_db())
    texts = [json.loads(raw)["text"] for raw in outbox.lrange(alerts.OUTBOX, 0, -1)]
    assert [text.split("\n")[0] for text in texts] == [
        "[ai-geo-audit · dev] ПРОВЕРКА: канал тревог"
    ]


def test_a242_check_without_redis_is_code_1_and_one_reason(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A242: Redis лежит — код 1 и одна строка причины: запись ERROR «ТРЕВОГА НЕ ПОСТАВЛЕНА»."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    use(monkeypatch, f"redis://127.0.0.1:{port}/0")
    traced(monkeypatch)
    assert command.main(["check"]) == 1
    out, err = capsys.readouterr()
    lines = [line for line in (out + err).splitlines() if line]
    assert len(lines) == 1 and json.loads(lines[0])["level"] == "ERROR"
    assert json.loads(lines[0])["message"].startswith(
        "ТРЕВОГА НЕ ПОСТАВЛЕНА — Redis не принимает соединение: ПРОВЕРКА «канал тревог»"
    )


@pytest.mark.parametrize("argv", [[], ["send"], ["check", "--now"]], ids=["none", "send", "extra"])
def test_a242_other_arguments_are_the_usage_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    """A242: команда одна — иначе строка использования в поток ошибок, код 2, тревоги нет."""
    calls = traced(monkeypatch)
    assert command.main(argv) == 2
    assert capsys.readouterr() == ("", command.USAGE + "\n")
    assert calls == []


def test_a242_bad_setting_at_import_is_words_without_traceback() -> None:
    """A242: негодная настройка при импорте — код 1, причина словами с именем переменной;
    `Traceback` и введённого значения нет во всём выводе (L29, L30)."""
    env = process_env(APP_ENV=LABEL)
    done = run(["-m", "ai_geo_audit.ops.alerts", "check"], env)
    text = done.stdout + done.stderr
    assert done.returncode == 1, text
    assert "Процесс «alerts» не стартует" in text and "APP_ENV" in text
    assert "Traceback" not in text and LABEL not in text


def test_a242_guarded_paths_do_not_leak_the_setting_secret(redis_url: str) -> None:
    """A242: предупреждение Python мимо журнала и запись, которая не собирается, — с секретом
    настроек: секрета нет ни в выводе, ни в потоке ошибок; каждая строка потока ошибок — запись
    нашего журнала (L54, L55); тревога поставлена, код 0."""
    env = process_env(REDIS_URL=redis_url, JWT_SECRET=LABEL)
    done = run(["-c", NOISY], env)
    text = done.stdout + done.stderr
    assert done.returncode == 0, text
    assert done.stdout == "поставлено в исходящие\n"
    assert LABEL not in text and "***" in text
    assert [line for line in done.stderr.splitlines() if not line.startswith("{")] == []
