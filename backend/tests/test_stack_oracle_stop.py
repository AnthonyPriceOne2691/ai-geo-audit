"""Части шагов остановки и падения api оракула без Docker (A88, первая, вторая и четвёртая
строки): записи uvicorn в журнале, итоги шагов, вердикт разбором пакета, ожидание перезапуска и
возврат api при красном. Модуль `scripts/stack_oracle_stop.py` лежит вне пакета."""

from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import dbtools
import pytest

sys.path.insert(0, str(dbtools.ROOT / "scripts"))
stop = importlib.import_module("stack_oracle_stop")


def entry(logger: str, message: str) -> str:
    """Строка журнала api нашего формата: JSON, `LOG_FORMAT=json`."""
    fields = {"ts": "2026-11-24T11:00:00.000Z", "level": "INFO", "logger": logger}
    return json.dumps(fields | {"message": message})


FINISHED = entry("uvicorn.error", "Finished server process [7]")
DOWN = {"State": {"Status": "exited", "ExitCode": 143}, "RestartCount": 0}


@pytest.mark.parametrize(
    ("lines", "count"),
    [
        ([FINISHED], 1),
        ([entry("uvicorn.error", "Shutting down"), FINISHED, FINISHED], 2),
        ([entry("ai_geo_audit.api", "Finished server process [7]")], 0),
        ([entry("uvicorn.error", "Waiting for application shutdown.")], 0),
        (["INFO:     Finished server process [7]"], 0),
        (['["uvicorn.error", "Finished server process"]'], 0),
        (['{"logger": "uvicorn.error"}'], 0),
        ([], 0),
    ],
    ids=[
        "запись",
        "две",
        "чужой-логгер",
        "другой-текст",
        "не-наш-формат",
        "не-объект",
        "без-текста",
        "пусто",
    ],
)
def test_a88_uvicorn_records_of_our_format_are_counted(lines: list[str], count: int) -> None:
    """A88, первая строка: запись — JSON нашего формата от логгера `uvicorn.error` с текстом
    «Finished server process»; другой логгер, текст, формат и не объект — не в счёт."""
    assert stop.finished_count("\n".join(lines)) == count


@pytest.mark.parametrize(
    ("change", "seconds", "said", "later", "why"),
    [
        ({}, 1.2, "остановлен", "exited", ""),
        (
            {"State": {"Status": "exited", "ExitCode": 137}},
            1.2,
            "убит по сроку",
            "exited",
            "код 137",
        ),
        ({"State": {"Status": "exited", "ExitCode": 0}}, 1.2, "остановлен", "exited", "код 0"),
        ({"State": {"Status": "running", "ExitCode": 0}}, 1.2, "упал", "exited", "running, код 0"),
        ({}, 10.0, "остановлен", "exited", "остановка 10.0 с"),
        ({}, 1.2, "упал", "exited", "разбор — «упал»"),
        ({"RestartCount": 1}, 1.2, "остановлен", "exited", "перезапусков 1"),
        ({}, 1.2, "остановлен", "restarting", "после остановки — restarting"),
    ],
    ids=["ок", "137", "код-0", "не-остановлен", "10-с", "разбор", "перезапуск", "поднялся"],
)
def test_a88_stop_is_exited_143_fast_judged_stopped_and_stays_down(
    change: dict[str, Any], seconds: float, said: str, later: str, why: str
) -> None:
    """A88, первая строка: `exited` с кодом 143 быстрее 10 с, разбор — «остановлен», перезапусков
    0, контейнер не поднялся сам; иначе — красное с причиной."""
    found = "; ".join(stop.stop_problems(DOWN | change, seconds, said, later))
    assert (why in found) if why else not found, found


@pytest.mark.parametrize(
    ("restarts", "seconds", "net", "why"),
    [
        ((0, 1), 12.5, 0, ""),
        ((0, 1), 50.0, 0, ""),
        ((0, 0), 12.5, 0, "перезапусков 0 → 0"),
        ((0, 2), 12.5, 0, "перезапусков 0 → 2"),
        ((0, 1), 50.1, 0, "healthy через 50.1 с"),
        ((0, 1), 12.5, 1, "из сети стека — код 1"),
    ],
    ids=["ок", "ровно-50", "без-перезапуска", "два", "дольше-50", "сеть"],
)
def test_a88_crash_is_one_restart_healthy_within_50_s_and_heard_from_the_network(
    restarts: tuple[int, int], seconds: float, net: int, why: str
) -> None:
    """A88, четвёртая строка: перезапусков ровно +1, снова healthy не позже 50 с, здоровье из сети
    стека — код 0 (L50); иначе — красное с причиной."""
    found = "; ".join(stop.crash_problems(restarts, seconds, net))
    assert (why in found) if why else not found, found


@pytest.mark.parametrize(
    ("found", "child"),
    [
        (["7", "1"], True),
        (["7", "0"], False),
        ([], False),
        (["x", "1"], False),
        (["7", "1", "9", "1"], False),
    ],
    ids=["дочерний-init", "родитель-0", "не-найден", "не-число", "два"],
)
def test_a88_uvicorn_must_be_the_one_child_of_init(found: list[str], child: bool) -> None:
    """A88, четвёртая строка: убивается ровно один процесс `python -m ai_geo_audit.api`, и его
    родитель — init (pid 1): без init сигнал первому процессу пространства не дойдёт."""
    assert stop.child_of_init(found) is child


#: Процессы контейнера api в миниатюре `/proc`: командная строка и `stat` — init с той же командой
#: в хвосте, uvicorn — его сын, проба здоровья Docker — сын нулевого процесса.
PROCESSES = {
    "1": (b"/sbin/docker-init\0--\0python\0-m\0ai_geo_audit.api\0", "1 (docker-init) S 0 1 1"),
    "7": (b"python\0-m\0ai_geo_audit.api\0", "7 (python) S 1 7 7"),
    "33": (b"python\0-m\0ai_geo_audit.api.probe\0", "33 (python) S 0 33 33"),
}


def test_a88_search_takes_only_uvicorn_by_argv_words(tmp_path: Path) -> None:
    """A88, четвёртая строка: поиск сверяет слова argv — ровно `python -m ai_geo_audit.api`; init с
    той же командой в хвосте и проба здоровья `ai_geo_audit.api.probe` рядом с uvicorn не
    находятся; процесс, исчезнувший посреди обхода, и записи не-числа пропускаются."""
    for pid, (cmdline, stat) in PROCESSES.items():
        (tmp_path / pid).mkdir()
        (tmp_path / pid / "cmdline").write_bytes(cmdline)
        (tmp_path / pid / "stat").write_text(stat, encoding="utf-8")
    (tmp_path / "40").mkdir()
    (tmp_path / "self").mkdir()
    done = subprocess.run(
        [sys.executable, "-c", stop.FIND, str(tmp_path)],
        capture_output=True,
        encoding="utf-8",
        check=True,
    )
    assert done.stdout.split() == ["7", "1"]


class Answer:
    """Подставной исполнитель Docker: один ответ на всё, вызовы — по порядку."""

    def __init__(self, out: str) -> None:
        self.out, self.calls, self.containers = out, [], []

    def __call__(self, *args: str, **kw: object) -> str:
        self.calls.append(args)
        return self.out


def test_a88_verdict_is_asked_of_the_package_in_the_built_image() -> None:
    """A88, вторая строка: вердикт — разбор пакета в собранном образе без сети: код выхода, запись
    и команда остановки — аргументами сценария; контейнер — в учёте уборки."""
    docker = Answer("остановлен\n")
    run = SimpleNamespace(name="o", image="o-backend:oracle", docker=docker)
    assert stop.judge(run, 143, True) == "остановлен"
    assert docker.containers == ["o-verdict"]
    flags = ("run", "--rm", "--name", "o-verdict", "--network", "none", "--entrypoint", "python")
    assert docker.calls == [(*flags, "o-backend:oracle", "-c", stop.VERDICT, "143", "1", "1")]


@pytest.mark.parametrize(
    ("args", "said"),
    [
        (("143", "1", "1"), "остановлен"),
        (("137", "0", "1"), "убит по сроку"),
        (("143", "1", "0"), "упал"),
    ],
    ids=["остановлен", "убит", "упал"],
)
def test_a88_verdict_script_answers_with_the_package_words(
    args: tuple[str, ...], said: str
) -> None:
    """A88, вторая строка: сценарий вердикта — разбор `ai_geo_audit.ops.stop_verdict`: код, запись и
    команда — аргументами, ответ — слово вердикта."""
    done = subprocess.run(
        [sys.executable, "-c", stop.VERDICT, *args],
        capture_output=True,
        encoding="utf-8",
        env=os.environ | {"PYTHONIOENCODING": "utf-8"},
        check=True,
    )
    assert done.stdout == said + "\n"


def clock(*ticks: float) -> SimpleNamespace:
    """Подставные часы шага: монотонное время по списку, сон — мгновенный."""
    times: Iterator[float] = iter(ticks)
    return SimpleNamespace(monotonic=lambda: next(times), sleep=lambda seconds: None)


def test_a88_restart_is_awaited_by_the_counter(monkeypatch: pytest.MonkeyPatch) -> None:
    """A88, четвёртая строка: перезапуск ждётся по счётчику `RestartCount`; не вырос в срок —
    красное с пределом."""
    counts = iter([0, 0, 1])
    monkeypatch.setattr(stop, "inspect", lambda run, service: {"RestartCount": next(counts)})
    monkeypatch.setattr(stop, "time", clock(0.0, 1.0, 2.0, 3.0))
    assert stop.wait_restart(SimpleNamespace(), 0, 50) == 1
    monkeypatch.setattr(stop, "inspect", lambda run, service: {"RestartCount": 0})
    monkeypatch.setattr(stop, "time", clock(0.0, 30.0, 60.0))
    with pytest.raises(stop.StepError, match="api не перезапущен за 50 с"):
        stop.wait_restart(SimpleNamespace(), 0, 50)


def stack(
    monkeypatch: pytest.MonkeyPatch, logs: list[str], fail: str = "", found: str = "7 1"
) -> list[str]:
    """Подставные compose, состояние api и ожидания: шаги A88 без Docker; список — что звали."""
    seen: list[str] = []
    answers = iter(logs)

    def compose(run: object, *args: str, **kw: object) -> str:
        seen.append(args[0] if args[0] != "exec" else f"exec {kw.get('check', True)}")
        if args[0] == fail:
            raise stop.StepError(f"{fail} — код 1")
        return next(answers) if args[0] == "logs" else found

    monkeypatch.setattr(stop, "compose", compose)
    monkeypatch.setattr(stop, "STILL", 0)
    monkeypatch.setattr(stop, "inspect", lambda run, service: DOWN)
    monkeypatch.setattr(stop, "judge", lambda run, code, finished: ("упал", "остановлен")[finished])
    monkeypatch.setattr(stop, "wait_health", lambda *args: seen.append("healthy") or 30.0)
    monkeypatch.setattr(stop, "wait_restart", lambda run, before, limit: before + 1)
    monkeypatch.setattr(stop, "net_health", lambda run: ("8107", SimpleNamespace(returncode=0)))
    return seen


def test_a88_stop_step_is_green_with_a_new_record_and_returns_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A88, первая строка: новая запись uvicorn после команды — разбор «остановлен», шаг зелёный;
    api снова запущен и ждёт healthy (L88)."""
    seen = stack(monkeypatch, [FINISHED, f"{FINISHED}\n{FINISHED}"])
    monkeypatch.setattr(stop, "time", clock(10.0, 11.5))
    assert stop.step_stop(SimpleNamespace()).startswith("exited с кодом 143 за 1.5 с")
    assert seen == ["logs", "stop", "logs", "start", "healthy"]


def test_a88_stop_step_without_a_new_record_or_failed_is_red_and_returns_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A88, первая строка: записи после команды нет (старая — от прежней остановки) — разбор
    «упал», красное; сбой команды — красное с причиной; api возвращается в обоих случаях (L88)."""
    seen = stack(monkeypatch, [FINISHED, FINISHED])
    monkeypatch.setattr(stop, "time", clock(10.0, 11.5))
    with pytest.raises(stop.StepError, match="разбор — «упал»"):
        stop.step_stop(SimpleNamespace())
    assert seen[-2:] == ["start", "healthy"]
    seen = stack(monkeypatch, [FINISHED], fail="stop")
    monkeypatch.setattr(stop, "time", clock(10.0))
    with pytest.raises(stop.StepError, match="stop — код 1"):
        stop.step_stop(SimpleNamespace())
    assert seen == ["logs", "stop", "start", "healthy"]


def test_a88_crash_step_kills_the_child_of_init_and_returns_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A88, четвёртая строка: поиск uvicorn, сигнал без проверки кода (контейнер умирает вместе с
    процессом), перезапуск +1, healthy, сеть стека — зелёное; api возвращается (L88)."""
    seen = stack(monkeypatch, [])
    monkeypatch.setattr(stop, "time", clock(5.0))
    assert stop.step_crash(SimpleNamespace()).startswith("убит pid 7 — дочерний процесс init")
    assert seen == ["exec True", "exec False", "healthy", "start", "healthy"]


def test_a88_crash_step_without_a_child_of_init_or_a_restart_is_red(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A88, четвёртая строка: uvicorn не дочерний init — красное до сигнала; перезапуска нет —
    красное, api запущен снова (L88)."""
    seen = stack(monkeypatch, [], found="1 0")
    with pytest.raises(stop.StepError, match="не дочерний процесс init"):
        stop.step_crash(SimpleNamespace())
    assert seen == ["exec True"]

    def never(run: object, before: int, limit: float) -> int:
        raise stop.StepError(f"api не перезапущен за {limit:g} с")

    seen = stack(monkeypatch, [])
    monkeypatch.setattr(stop, "wait_restart", never)
    monkeypatch.setattr(stop, "time", clock(5.0))
    with pytest.raises(stop.StepError, match="api не перезапущен за 50 с"):
        stop.step_crash(SimpleNamespace())
    assert seen == ["exec True", "exec False", "start", "healthy"]
