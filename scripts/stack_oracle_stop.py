"""Шаги оракула остановки и падения api (A88) на поднятом стеке: мягкая остановка — код 143 с
записью uvicorn, быстрее 10 с и без перезапуска; падение uvicorn изнутри контейнера — перезапуск
ровно +1 и снова healthy не позже 50 с. Вердикт остановки — разбор пакета
`ai_geo_audit.ops.stop_verdict` в собранном образе: оракул на машине — стандартная библиотека, а
разбор тот же, что возьмёт сторож Ч1.14.

Шаги ломают api и стоят после шагов web и защиты стека, до шага A67; каждый возвращает api
healthy, и красный тоже.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from stack_oracle_compose import StepError, compose, inspect, net_health, wait_health

if TYPE_CHECKING:
    from stack_oracle import Run

#: Запись uvicorn в конце процесса — наш формат журнала, логгер `uvicorn.error` (A88).
LOGGER, FINISHED = "uvicorn.error", "Finished server process"
#: Остановка: код 143 (128 + 15), быстрее 10 с, вердикт разбора; 2 с — контейнер не поднялся сам.
STOP_CODE, STOP_LIMIT, STILL, STOPPED = 143, 10.0, 2.0, "остановлен"
#: Падение: снова healthy не позже 50 с от сигнала (20 с + 3 × 10 с, как A69).
CRASH_LIMIT = 50.0
#: Вердикт разбором пакета в образе: код выхода, запись и команда остановки — аргументами.
VERDICT = (
    "import sys\n"
    "from ai_geo_audit.ops.stop_verdict import verdict\n"
    "code, finished, stopped = sys.argv[1:]\n"
    "print(verdict(int(code), finished=finished == '1', stopped=stopped == '1'))\n"
)
#: Процессы `python -m ai_geo_audit.api` в контейнере: pid и pid родителя — у дочернего
#: процесса init это 1. Сверяются слова argv, а не префикс строки: проба здоровья
#: `python -m ai_geo_audit.api.probe` и init с той же командой в хвосте не находятся. Ищет
#: процесс того же uid, что и uvicorn; корень — аргументом, без него `/proc` (тест — миниатюра).
FIND = """
import os, pathlib, sys
for proc in pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/proc").iterdir():
    if not proc.name.isdigit() or int(proc.name) == os.getpid():
        continue
    try:
        argv = (proc / "cmdline").read_bytes().split(b"\\0")
        parent = (proc / "stat").read_text().rsplit(")", 1)[1].split()[1]
    except OSError:
        continue
    if argv[:3] == [b"python", b"-m", b"ai_geo_audit.api"]:
        print(proc.name, parent)
"""


def finished_count(log: str) -> int:
    """A88: записей uvicorn «Finished server process» нашего формата в журнале api — JSON от
    логгера `uvicorn.error`; строки другого вида не в счёт."""
    count = 0
    for line in log.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        ours = isinstance(entry, dict) and entry.get("logger") == LOGGER
        count += ours and FINISHED in str(entry.get("message"))
    return count


def stop_problems(state: dict[str, Any], seconds: float, said: str, later: str) -> list[str]:
    """A88, первая строка: `exited` с кодом 143 быстрее 10 с, разбор — «остановлен», перезапусков
    0, контейнер не поднялся сам."""
    status, code = state["State"]["Status"], state["State"]["ExitCode"]
    problems = [f"{status}, код {code}"] * (status != "exited" or code != STOP_CODE)
    problems += [f"остановка {seconds:.1f} с"] * (seconds >= STOP_LIMIT)
    problems += [f"разбор — «{said}»"] * (said != STOPPED)
    problems += [f"перезапусков {state['RestartCount']}"] * (state["RestartCount"] != 0)
    return problems + [f"после остановки — {later}"] * (later != "exited")


def crash_problems(restarts: tuple[int, int], seconds: float, net: int) -> list[str]:
    """A88, четвёртая строка: перезапусков ровно +1, снова healthy не позже 50 с, здоровье из сети
    стека — код 0 (L50)."""
    before, after = restarts
    problems = [f"перезапусков {before} → {after}"] * (after != before + 1)
    problems += [f"healthy через {seconds:.1f} с"] * (seconds > CRASH_LIMIT)
    return problems + [f"из сети стека — код {net}"] * bool(net)


def child_of_init(found: list[str]) -> bool:
    """Ровно один процесс `python -m ai_geo_audit.api`, и его родитель — init: без init сигнал
    первому процессу пространства не дойдёт."""
    return len(found) == 2 and found[0].isdigit() and found[1] == "1"


def judge(run: Run, code: int, finished: bool) -> str:
    """Вердикт остановки после команды — разбор пакета в собранном образе без сети (A88)."""
    container = f"{run.name}-verdict"
    run.docker.containers.append(container)
    flags = ["--rm", "--name", container, "--network", "none", "--entrypoint", "python"]
    args = [str(code), str(int(finished)), "1"]
    return run.docker("run", *flags, run.image, "-c", VERDICT, *args).strip()


def restore(run: Run) -> None:
    """api снова запущен и healthy: шаги A88 возвращают его и при красном."""
    compose(run, "start", "api", limit="up")
    wait_health(run, "api", "healthy", 90)


def api_log(run: Run) -> str:
    return compose(run, "logs", "--no-color", "--no-log-prefix", "api", limit="docker")


def step_stop(run: Run) -> str:
    """A88, первая строка: `docker compose stop api` — `exited` с кодом 143 быстрее 10 с; после
    команды в журнале новая запись uvicorn «Finished server process» — старые от прежних
    остановок не в счёт; разбор — «остановлен», перезапусков 0, сам контейнер не поднимается."""
    try:
        before = finished_count(api_log(run))
        start = time.monotonic()
        compose(run, "stop", "api", limit="up")
        seconds = time.monotonic() - start
        state = inspect(run, "api")
        said = judge(run, state["State"]["ExitCode"], finished_count(api_log(run)) > before)
        time.sleep(STILL)
        later = inspect(run, "api")["State"]["Status"]
    finally:
        restore(run)
    if problems := stop_problems(state, seconds, said, later):
        raise StepError("; ".join(problems))
    return f"exited с кодом 143 за {seconds:.1f} с; новая запись uvicorn; разбор — «{said}»"


def wait_restart(run: Run, before: int, limit: float) -> int:
    """Ждать перезапуска api докером после падения: счётчик `RestartCount` вырос."""
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        count: int = inspect(run, "api")["RestartCount"]
        if count > before:
            return count
        time.sleep(0.5)
    raise StepError(f"api не перезапущен за {limit:g} с")


def step_crash(run: Run) -> str:
    """A88, четвёртая строка: uvicorn — дочерний процесс init с командой `python -m
    ai_geo_audit.api` — убит SIGKILL изнутри контейнера процессом того же uid, а не `docker
    kill`: перезапусков ровно +1, снова healthy не позже 50 с, здоровье из сети стека — 200."""
    before: int = inspect(run, "api")["RestartCount"]
    found = compose(run, "exec", "-T", "api", "python", "-c", FIND, limit="docker").split()
    if not child_of_init(found):
        raise StepError(f"uvicorn — не дочерний процесс init: {found}")
    try:
        start = time.monotonic()
        kill = f"import os, signal; os.kill({found[0]}, signal.SIGKILL)"
        compose(run, "exec", "-T", "api", "python", "-c", kill, limit="docker", check=False)
        after = wait_restart(run, before, CRASH_LIMIT)
        seconds = wait_health(run, "api", "healthy", 90) - start
        net = net_health(run)[1].returncode
    finally:
        restore(run)
    if problems := crash_problems((before, after), seconds, net):
        raise StepError("; ".join(problems))
    healthy = f"healthy через {seconds:.1f} с"
    return f"убит pid {found[0]} — дочерний процесс init; перезапусков +1; {healthy}; сеть — 200"


#: Шаги остановки и падения api: после шагов защиты стека, до A67; остановка — до падения.
STOP_STEPS: list[tuple[str, str, Callable[[Run], str], bool]] = [
    ("A88", "остановка api", step_stop, False),
    ("A88", "падение uvicorn изнутри контейнера", step_crash, False),
]
