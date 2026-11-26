"""Шаги оракула для очереди «Учёток хранилищ» (A171): Redis с паролем.

Первая строка — ответы `redis-cli` разовому контейнеру redis в сети `data`: без пароля, с
неверным и с верным. Вторая — устройство запущенного redis: здоровье и записи проверки, команда в
описании контейнера, главный процесс, его пользователь и вход, процессы контейнера и мягкая
остановка — последним шагом прогона. Пароль — только в окружении команды (`run -e ИМЯ` без
значения, проба 3 среза 0), не в её строке (L84).
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from stack_oracle_compose import StepError, compose_base, compose_result, inspect, stack_env

if TYPE_CHECKING:
    import subprocess

    from stack_oracle import Run

#: Разовый `redis-cli` сети `data`; пароль — `REDISCLI_AUTH` из окружения команды.
CLI = ("run", "--rm", "--no-deps", "-e", "REDISCLI_AUTH", "redis", "redis-cli", "-h", "redis")
#: Внутри redis: имя главного процесса, его uid, uid пользователя `redis` образа, вход PID 1.
INSIDE = "; ".join(
    (
        "cat /proc/1/comm",
        "awk '/^Uid/{print $2}' /proc/1/status",
        "id -u redis",
        "readlink /proc/1/fd/0",
    )
)
#: Мягкая остановка — быстрее срока докера по умолчанию: по нему он убивает, код 137.
STOP_LIMIT = 10.0


def _text(data: bytes) -> str:
    return data.decode("utf-8", "replace").strip()


def ping(run: Run, auth: str | None) -> subprocess.CompletedProcess[bytes]:
    """`redis-cli -h redis ping` разовым контейнером; `auth` — значение `REDISCLI_AUTH` в окружении
    команды, None — переменной нет вовсе."""
    env = {name: value for name, value in stack_env(run).items() if name != "REDISCLI_AUTH"}
    if auth is not None:
        env["REDISCLI_AUTH"] = auth
    return run.docker.result(*compose_base(run), *CLI, "ping", env=env)


def step_auth(run: Run) -> str:
    """A171, первая строка: без пароля — `NOAUTH…`; с неверным — `WRONGPASS…` в stderr, ответа
    `PONG` нет; с верным — `PONG`."""
    none = ping(run, None)
    if "NOAUTH" not in _text(none.stdout) or "PONG" in _text(none.stdout):
        raise StepError(f"без пароля — ответ «{_text(none.stdout)[:60]}», а не NOAUTH")
    wrong = ping(run, f"wrong-{secrets.token_hex(8)}")
    if "WRONGPASS" not in _text(wrong.stderr) or "PONG" in _text(wrong.stdout):
        answer, err = _text(wrong.stdout)[:40], _text(wrong.stderr)[:60]
        why = f"неверный пароль — ответ «{answer}», stderr «{err}»: нужен WRONGPASS без PONG"
        raise StepError(why)
    right = ping(run, run.redis_password)
    if _text(right.stdout) != "PONG":
        raise StepError(f"верный пароль — ответ «{_text(right.stdout)[:60]}», а не PONG")
    return "без пароля — NOAUTH; неверный — WRONGPASS в stderr, PONG нет; верный — PONG"


def top_commands(text: str) -> list[str]:
    """Строки процессов из `docker compose top`: столбец CMD — последний, с пробелами внутри (вид
    5.x — одна таблица со столбцами SERVICE и #, проба 5 среза 0; 2.x — блок на контейнер)."""
    commands: list[str] = []
    column: int | None = None
    for line in text.splitlines():
        words = line.split()
        if "CMD" in words and "PID" in words:
            column = words.index("CMD")
        elif not words:
            column = None
        elif column is not None and len(words) > column:
            commands.append(line.split(None, column)[column].strip())
    return commands


def _health(run: Run) -> tuple[list[object], str]:
    """Здоровье и записи проверки здоровья redis; отказ — не healthy или запись не `PONG`."""
    info = inspect(run, "redis")
    health = info["State"].get("Health") or {}
    outputs = sorted({str(log.get("Output", "")).strip() for log in health.get("Log") or []})
    if health.get("Status") != "healthy" or outputs != ["PONG"]:
        raise StepError(f"здоровье redis — {health.get('Status')}, записи проверки — {outputs}")
    command: list[object] = info["Config"].get("Cmd") or []
    return command, info["Config"].get("User", "")


def step_process(run: Run) -> str:
    """A171, вторая строка: healthy, записи проверки — `PONG`; `Config.Cmd` — строка `sh -c` с
    нераскрытым `$REDIS_PASSWORD`; главный процесс — `redis-server` с PID 1 под пользователем
    `redis` образа, не 0, вход — канал; процесс контейнера один — `redis-server *:6379`, `sh` нет;
    в конце — `docker compose stop redis`: код 0 быстрее 10 с."""
    command, user = _health(run)
    if command[:2] != ["sh", "-c"] or "$REDIS_PASSWORD" not in str(command[-1]):
        raise StepError("Config.Cmd — не строка sh -c с нераскрытым $REDIS_PASSWORD")
    inside = compose_result(run, "exec", "-T", "redis", "sh", "-c", INSIDE)
    comm, uid, image_uid, stdin = [*_text(inside.stdout).splitlines(), "", "", "", ""][:4]
    own_user = uid not in ("", "0") and uid == image_uid
    if comm != "redis-server" or not own_user or not stdin.startswith("pipe:"):
        raise StepError(f"PID 1 — {comm}, uid {uid} (redis — {image_uid}), вход — {stdin}")
    processes = top_commands(_text(compose_result(run, "top", "redis").stdout))
    if processes != ["redis-server *:6379"]:
        raise StepError(f"процессы redis — {processes}")
    started = time.monotonic()
    stopped = compose_result(run, "stop", "redis")
    took = time.monotonic() - started
    code = inspect(run, "redis")["State"].get("ExitCode")
    if stopped.returncode or code != 0 or took >= STOP_LIMIT:
        raise StepError(f"остановка — код {stopped.returncode}, выход {code}, {took:.1f} с")
    return (
        f"healthy, записи — PONG; пользователь {user}; PID 1 — redis-server, uid {uid}, вход —"
        f" канал; процессы — redis-server *:6379; остановка — код 0 за {took:.1f} с"
    )


REDIS_STEPS: list[tuple[str, str, Callable[[Run], str], bool]] = [
    ("A171", "очередь: без пароля и с неверным — отказ, с верным — PONG", step_auth, False),
    ("A171", "redis: процесс, пользователь, вход и мягкая остановка", step_process, False),
]
