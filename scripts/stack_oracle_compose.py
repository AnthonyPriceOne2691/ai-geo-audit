"""Шаги оракула стека Ч1.5а на поднятом compose (A63, A65–A67, A69, A73).

Модуль ничего не знает о сборке: стек — по образу, который собрал `stack_oracle.py`. Команды —
через исполнителя прогона: своё имя стека, файл compose назван явно, переменные — окружением
команды. Импорт только в одну сторону: `stack_oracle.py` берёт отсюда шаги и общие части.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from stack_oracle import Run

COMPOSE = "docker-compose.yml"


#: Адрес web для доверия api — петля, выдуманный: сам web — Ч1.5б.
WEB_ADDRESS = "127.0.0.1"


DB_URL = "postgresql+asyncpg://audit:{}@db:5432/audit"


APP_UID = "10001"


#: Запрос здоровья из разового контейнера в сети стека тем же образом: слышит ли api сеть.
NET_PROBE = """
import sys, urllib.request
try:
    status = urllib.request.urlopen("http://api:{port}/api/health", timeout=5).status
except OSError as error:
    status = f"{{type(error).__name__}}: {{error}}"
print(status)
sys.exit(0 if status == 200 else 1)
"""


class StepError(RuntimeError):
    """Шаг красный; текст — причина без секретов."""


def parse_time(text: str) -> datetime | None:
    """Время Docker (RFC 3339, до наносекунд); нулевое `0001-01-01…` — None: не стартовал."""
    found = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d{1,6})?", text or "")
    if not found or found[1].startswith("0001-"):
        return None
    return datetime.fromisoformat(found[1] + (found[2] or "")).replace(tzinfo=UTC)


def top_uids(text: str) -> set[str]:
    """Пользователи процессов из `docker compose top`: столбец UID под строкой заголовка. Вид у
    версий разный: у 5.x первые столбцы — SERVICE и #, у 2.x — блок на контейнер с именем над
    заголовком и пустой строкой между блоками."""
    uids: set[str] = set()
    column: int | None = None
    for line in text.splitlines():
        words = line.split()
        if "UID" in words and "PID" in words:
            column = words.index("UID")
        elif not words:
            column = None
        elif column is not None and len(words) > column:
            uids.add(words[column])
    return uids


def stack_env(run: Run, wrong: str | None = None) -> dict[str, str]:
    """Окружение compose: переменные сервера стека без чужих `COMPOSE_*`; `wrong` — неверный
    пароль у migrate (A67)."""
    clean = {k: v for k, v in os.environ.items() if not k.startswith("COMPOSE_")}
    return clean | {
        "BACKEND_IMAGE": run.image,
        "DB_PASSWORD": run.password,
        "MIGRATE_DATABASE_URL": DB_URL.format(wrong or run.password),
        "API_DATABASE_URL": DB_URL.format(run.password),
        "JWT_SECRET": run.jwt,
        "WEB_ADDRESS": WEB_ADDRESS,
        "KEYS_MASTER_KEY_HOST_FILE": str(run.copy.parent / "master.key"),
    }


def compose_base(run: Run) -> list[str]:
    """Своё имя стека, файл назван явно, `.env` копии с канарейками не читается."""
    return ["compose", "-p", run.name, "-f", str(run.copy / COMPOSE), "--env-file", os.devnull]


def compose(run: Run, *args: str, wrong: str | None = None, **kw: Any) -> str:
    return run.docker(*compose_base(run), *args, env=stack_env(run, wrong), **kw)


def compose_result(
    run: Run, *args: str, wrong: str | None = None, **kw: Any
) -> subprocess.CompletedProcess[bytes]:
    return run.docker.result(*compose_base(run), *args, env=stack_env(run, wrong), **kw)


def inspect(run: Run, service: str) -> dict[str, Any]:
    """Состояние контейнера сервиса стека по `docker inspect`."""
    ids = compose(run, "ps", "--all", "--quiet", service, limit="docker").split()
    if not ids:
        raise StepError(f"контейнера {service} нет")
    found: dict[str, Any] = json.loads(run.docker("inspect", ids[0], limit="docker"))[0]
    return found


def cleanup(run: Run) -> list[str]:
    """Уборка своего имени: стек с томами, контейнеры и образы; остаток — по меткам compose."""
    if (run.copy / COMPOSE).is_file():
        with contextlib.suppress(StepError):
            compose(run, "down", "-v", "--remove-orphans", limit="up", check=False)
    left = run.docker.cleanup()
    mark = f"label=com.docker.compose.project={run.name}"
    for kind, flags in (("container", ["-a"]), ("volume", []), ("network", [])):
        ids = run.docker(kind, "ls", "-q", *flags, "--filter", mark, check=False).split()
        left += [f"{kind} {item}" for item in ids]
    return left


def output(done: subprocess.CompletedProcess[bytes]) -> str:
    return (done.stdout + done.stderr).decode("utf-8", "replace")


def health(run: Run, service: str) -> str | None:
    status: str | None = inspect(run, service)["State"].get("Health", {}).get("Status")
    return status


def wait_health(run: Run, service: str, status: str, limit: float) -> float:
    """Ждать состояния здоровья сервиса не дольше `limit` с; время монотонных часов."""
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        if health(run, service) == status:
            return time.monotonic()
        time.sleep(0.5)
    raise StepError(f"{service} не стал {status} за {limit:g} с")


def alembic(run: Run, command: str) -> list[str]:
    """Ревизии alembic из образа: `current` или `heads` разовым migrate."""
    out = compose(run, "run", "--rm", "-T", "--no-deps", "migrate", "alembic", command)
    return sorted(re.findall(r"(?m)^(\w+)(?: \(head\))?\s*$", out))


def step_rules_on_file(run: Run) -> str:
    """A73: правила на настоящем файле внутри собранного образа. Файл назван явно — чужой
    `docker-compose.override.yml` не подмешается; профили включены все; без `-p`."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("COMPOSE_")}
    base = ["compose", "-f", str(run.copy / COMPOSE), "--env-file", os.devnull, "--profile", "*"]
    raw = run.docker(*base, "config", "--no-interpolate", "--format", "json", env=env)
    services = run.docker(*base, "config", "--no-interpolate", "--services", env=env).split()
    container = f"{run.name}-rules"
    run.docker.containers.append(container)
    flags = ["--rm", "-i", "--name", container, "--network", "none", "--entrypoint", "python"]
    rules = ["-m", "ai_geo_audit.ops.stack_rules"]
    done = run.docker.result("run", *flags, run.image, *rules, stdin=raw.encode())
    lines = output(done).strip().splitlines()
    if done.returncode or lines[-1:] != [f"итог: сервисов {len(services)}, правил 16, нарушений 0"]:
        raise StepError(f"правила — код {done.returncode}: {' | '.join(lines[-5:])}")
    return f"{lines[-1]}; config --services — {len(services)}"


def step_up(run: Run) -> str:
    """A65, A63: стек одной командой — migrate вышел 0 до старта api, api healthy; ревизия в базе
    равна голове, обе — из образа; migrate и процессы api — от uid 10001."""
    done = compose_result(run, "up", "--detach", "--wait", "--wait-timeout", "240", limit="up")
    if done.returncode:
        raise StepError(f"up --wait — код {done.returncode}: {output(done)[-500:]}")
    migrate, api = inspect(run, "migrate"), inspect(run, "api")
    finished, started = (
        parse_time(migrate["State"]["FinishedAt"]),
        parse_time(api["State"]["StartedAt"]),
    )
    current, heads = alembic(run, "current"), alembic(run, "heads")
    uid = compose(run, "run", "--rm", "-T", "--no-deps", "migrate", "id", "-u").strip()
    uids = top_uids(compose(run, "top", "api", limit="docker"))
    problems = [f"migrate вышел с кодом {migrate['State']['ExitCode']}"] * bool(
        migrate["State"]["ExitCode"]
    )
    problems += ["api стартовал не после выхода migrate"] * (
        not (finished and started and started > finished)
    )
    problems += [f"api — {health(run, 'api')}"] * (health(run, "api") != "healthy")
    problems += [f"alembic current {current}, heads {heads}"] * (not current or current != heads)
    problems += [f"id -u в migrate — {uid}, процессы api — {sorted(uids)}"] * (
        uid != APP_UID or uids != {APP_UID}
    )
    if problems:
        raise StepError("; ".join(problems))
    return f"migrate — код 0 до старта api; alembic current = heads {current}; uid {APP_UID}"


def step_health(run: Run) -> str:
    """A69: api healthy не позже 50 с от старта (20 с + 3 × 10 с); проба внутри — код 0; запрос
    здоровья из разового контейнера в сети стека — 200: api слышит сеть, а не только петлю."""
    api = inspect(run, "api")
    started = parse_time(api["State"]["StartedAt"])
    oks = [parse_time(e["End"]) for e in api["State"]["Health"]["Log"] if e["ExitCode"] == 0]
    if started is None or not any(oks):
        raise StepError("api не стартовал или ни одной удачной проверки здоровья")
    seconds = (min(t for t in oks if t) - started).total_seconds()
    probe = compose_result(run, "exec", "-T", "api", "python", "-m", "ai_geo_audit.api.probe")
    port = dict(e.split("=", 1) for e in api["Config"]["Env"]).get("API_PORT", "8107")
    once = ["run", "--rm", "-T", "--no-deps", "--entrypoint", "python", "migrate", "-c"]
    net = compose_result(run, *once, NET_PROBE.format(port=port))
    if seconds > 50 or probe.returncode or net.returncode:
        raise StepError(
            f"healthy через {seconds:.1f} с; проба внутри — код {probe.returncode}; из сети "
            f"стека — {net.stdout.decode('utf-8', 'replace').strip()[-120:]}"
        )
    return (
        f"healthy через {seconds:.1f} с от старта api; проба в контейнере — код 0; из сети стека "
        f"http://api:{port}/api/health — 200"
    )


def step_repeat(run: Run) -> str:
    """A66: migrate второй раз — код 0, ревизия прежняя, «Running upgrade» в выводе нет."""
    before = alembic(run, "current")
    done = compose_result(run, "run", "--rm", "-T", "--no-deps", "migrate")
    after = alembic(run, "current")
    if done.returncode or "Running upgrade" in output(done) or after != before:
        raise StepError(f"повтор — код {done.returncode}, ревизия {before} → {after}")
    return f"код 0, ревизия {after}, «Running upgrade» нет"


def step_outage(run: Run) -> str:
    """A69: db остановлена — api unhealthy через 20–35 с; db снова healthy — api healthy не позже
    15 с; перезапусков api — 0."""
    compose(run, "stop", "db", limit="up")
    stopped = time.monotonic()
    unhealthy = wait_health(run, "api", "unhealthy", 60) - stopped
    compose(run, "start", "db", limit="up")
    db_back = wait_health(run, "db", "healthy", 90)
    back = wait_health(run, "api", "healthy", 60) - db_back
    restarts = inspect(run, "api")["RestartCount"]
    times = f"unhealthy через {unhealthy:.1f} с после остановки db, healthy через {back:.1f} с"
    if not 20 <= unhealthy <= 35 or back > 15 or restarts:
        raise StepError(f"{times} после db, перезапусков {restarts}")
    return f"{times} после возврата db; перезапусков 0"


def step_bad_migrate(run: Run) -> str:
    """A67: у migrate неверный пароль базы, у api верный — up --wait не 0; migrate назван с кодом
    выхода; api не стартовал (created, StartedAt нулевое); db healthy; пароля нет в выводе."""
    compose(run, "down", "--volumes", "--remove-orphans", limit="up")
    wrong = secrets.token_hex(12)
    done = compose_result(
        run, "up", "--detach", "--wait", "--wait-timeout", "240", wrong=wrong, limit="up"
    )
    migrate, api = inspect(run, "migrate"), inspect(run, "api")
    code = migrate["State"]["ExitCode"]
    seen = output(done) + compose(run, "logs", "--no-color", "migrate", limit="docker")
    problems = ["up --wait — код 0"] * (done.returncode == 0) + ["migrate — код 0"] * (code == 0)
    problems += [f"api — {api['State']['Status']}"] * (
        api["State"]["Status"] != "created" or parse_time(api["State"]["StartedAt"]) is not None
    )
    problems += [f"db — {health(run, 'db')}"] * (health(run, "db") != "healthy")
    problems += ["пароль в выводе"] * (wrong in seen or run.password in seen)
    if problems:
        raise StepError("; ".join(problems))
    return f"up --wait — код {done.returncode}; migrate — код выхода {code}; api — created"


#: Шаги стека: примеры, название, функция, нужен ли он следующим шагам.
STACK_STEPS: list[tuple[str, str, Callable[[Run], str], bool]] = [
    ("A73", "правила на настоящем docker-compose.yml", step_rules_on_file, False),
    ("A65 A63", "подъём стека и migrate", step_up, True),
    ("A69", "здоровье api после старта", step_health, False),
    ("A66", "повтор migrate", step_repeat, False),
    ("A69", "остановка и старт db", step_outage, False),
    ("A67", "неверный пароль у migrate", step_bad_migrate, False),
]
