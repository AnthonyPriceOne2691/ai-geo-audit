"""Шаги оракула стека Ч1.5а на поднятом compose (A63, A65–A67, A69, A73) и порт web с подсетью
`front` от имени стека (A82).

Модуль ничего не знает о сборке: стек — по образу, который собрал `stack_oracle.py`. Команды —
через исполнителя прогона: своё имя стека, файл compose назван явно, переменные — окружением
команды. Импорт только в одну сторону: `stack_oracle.py` берёт отсюда шаги и общие части.
"""

from __future__ import annotations

import contextlib
import hashlib
import ipaddress
import json
import os
import re
import secrets
import socket
import subprocess
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from stack_oracle import Run

COMPOSE = "docker-compose.yml"
#: Правил основного набора (A87): шестнадцать кодов Ч1.5а, семь A87 и `stop-grace` A88.
MAIN_RULES = 24
#: Боевое наложение и правил с флагом `--prod` (A89): 24 основного и три боевых.
PROD, PROD_RULES = "docker-compose.prod.yml", 27


DB_URL = "postgresql+asyncpg://audit:{}@db:5432/audit"


APP_UID = "10001"


#: Порт web и подсеть `front` (A82): ряд из SLOTS кандидатов, начало — от sha256 имени стека.
#: Подсети — /28 из 172.16.0.0/16: вне пулов, из которых докер даёт подсеть сетям без своей.
WEB_PORTS, FRONT_NET, SLOTS, TRIES = 18100, ipaddress.ip_address("172.16.0.0"), 256, 5
SUBNETS = "{{range .IPAM.Config}}{{.Subnet}} {{end}}"


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


def tag(run: Run, kind: str) -> str:
    """Свой образ прогона: имя стека, вид образа, метка `oracle`."""
    return f"{run.name}-{kind}:oracle"


def web_slot(name: str, busy: Callable[[int, str], bool]) -> tuple[int, str]:
    """A82: порт web и подсеть `front` — из ряда от имени стека; занятый кандидат — следующий,
    пять занятых — отказ словами."""
    start = int(hashlib.sha256(name.encode()).hexdigest()[:8], 16)
    for index in ((start + step) % SLOTS for step in range(TRIES)):
        port, subnet = WEB_PORTS + index, f"{FRONT_NET + 16 * index}/28"
        if not busy(port, subnet):
            return port, subnet
    raise StepError(f"порт web и подсеть front: заняты все {TRIES} кандидатов от имени {name}")


def busy_on(nets: list[str]) -> Callable[[int, str], bool]:
    """A82: кандидат занят, если порт на петле держит другой (проба bind) или подсеть пересекает
    сеть докера из `nets`."""

    def busy(port: int, subnet: str) -> bool:
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                return True
        net = ipaddress.ip_network(subnet)
        return any(net.overlaps(ipaddress.ip_network(other)) for other in nets)

    return busy


def slot(run: Run) -> tuple[int, str]:
    """Порт web и подсеть `front` прогона — выбираются один раз, по сетям докера на машине."""
    if "slot" not in run.facts:
        ids = run.docker("network", "ls", "-q", limit="docker").split()
        show = ["network", "inspect", "--format", SUBNETS, *ids]
        nets = run.docker(*show, limit="docker").split() if ids else []
        run.facts["slot"] = web_slot(run.name, busy_on(nets))
    found: tuple[int, str] = run.facts["slot"]
    return found


def web_address(subnet: str) -> str:
    """A83: постоянный адрес web — последний адрес узла подсети front: докер раздаёт адреса с
    начала подсети, и api с разовыми контейнерами его не займут."""
    return str(ipaddress.ip_network(subnet)[-2])


def stack_env(run: Run, wrong: str | None = None) -> dict[str, str]:
    """Окружение compose: переменные сервера стека без чужих `COMPOSE_*`; `wrong` — неверный
    пароль у migrate (A67); образ web, его порт, подсеть `front` и адрес в ней — свои у прогона
    (A82, A83)."""
    clean = {k: v for k, v in os.environ.items() if not k.startswith("COMPOSE_")}
    port, subnet = slot(run)
    return clean | {
        "BACKEND_IMAGE": run.image,
        "WEB_IMAGE": tag(run, "web"),
        "WEB_PORT": str(port),
        "FRONT_SUBNET": subnet,
        "DB_PASSWORD": run.password,
        "MIGRATE_DATABASE_URL": DB_URL.format(wrong or run.password),
        "API_DATABASE_URL": DB_URL.format(run.password),
        "JWT_SECRET": run.jwt,
        "WEB_ADDRESS": web_address(subnet),
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


def rules_on(run: Run, files: list[str], extra: list[str], count: int) -> str:
    """Правила внутри собранного образа на выводе compose по `files` — «сервисов N, правил
    `count`, нарушений 0». Файлы названы явно — чужой `docker-compose.override.yml` не
    подмешается; профили включены все; без `-p`. `extra` — флаги правил."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("COMPOSE_")}
    named = [part for file in files for part in ("-f", str(run.copy / file))]
    base = ["compose", *named, "--env-file", os.devnull, "--profile", "*"]
    raw = run.docker(*base, "config", "--no-interpolate", "--format", "json", env=env)
    services = run.docker(*base, "config", "--no-interpolate", "--services", env=env).split()
    container = f"{run.name}-rules{len(files)}"
    run.docker.containers.append(container)
    flags = ["--rm", "-i", "--name", container, "--network", "none", "--entrypoint", "python"]
    rules = ["-m", "ai_geo_audit.ops.stack_rules", *extra]
    done = run.docker.result("run", *flags, run.image, *rules, stdin=raw.encode())
    lines = output(done).strip().splitlines()
    total = f"итог: сервисов {len(services)}, правил {count}, нарушений 0"
    if done.returncode or lines[-1:] != [total]:
        raise StepError(f"правила — код {done.returncode}: {' | '.join(lines[-5:])}")
    return f"{lines[-1]}; config --services — {len(services)}"


def step_rules_on_file(run: Run) -> str:
    """A73, A87: правила на настоящем основном файле — «сервисов 5, правил 24», нарушений 0."""
    return rules_on(run, [COMPOSE], [], MAIN_RULES)


def step_rules_on_prod(run: Run) -> str:
    """A89, вторая строка: правила с флагом `--prod` на основном файле с боевым наложением —
    «сервисов 5, правил 27», нарушений 0. Наложение оракул не поднимает: драйвера journald может
    не быть на машине; первый подъём — Ч1.6."""
    return rules_on(run, [COMPOSE, PROD], ["--prod"], PROD_RULES)


def service_log(run: Run, service: str) -> str:
    """Хвост журнала сервиса для причины отказа: alembic пишет «FAILED: …» в вывод migrate, nginx
    — отказ конфига и ответы проверке здоровья в вывод web, а не в вывод `up` (L89). Пароль и
    секрет стека заменены звёздочками (A67: пароля нет в выводе)."""
    log = compose(run, "logs", "--no-color", "--tail", "20", service, limit="docker", check=False)
    tail = " | ".join(log.strip().splitlines()[-6:])
    for secret in (run.password, run.jwt):
        tail = tail.replace(secret, "***")
    return f"журнал {service}: {tail}"


def step_up(run: Run) -> str:
    """A65, A63: стек одной командой — migrate вышел 0 до старта api, api healthy; ревизия в базе
    равна голове, обе — из образа; migrate и процессы api — от uid 10001."""
    done = compose_result(run, "up", "--detach", "--wait", "--wait-timeout", "240", limit="up")
    if done.returncode:
        logs = "; ".join(service_log(run, service) for service in ("migrate", "web"))
        raise StepError(f"up --wait — код {done.returncode}: {output(done)[-500:]}; {logs}")
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


def net_health(run: Run) -> tuple[str, subprocess.CompletedProcess[bytes]]:
    """Запрос здоровья api из разового контейнера в сети стека тем же образом (A69, A88; L50):
    слышит ли api сеть, а не только петлю; порт — из окружения контейнера api."""
    env = inspect(run, "api")["Config"]["Env"]
    port: str = dict(e.split("=", 1) for e in env).get("API_PORT", "8107")
    once = ["run", "--rm", "-T", "--no-deps", "--entrypoint", "python", "migrate", "-c"]
    return port, compose_result(run, *once, NET_PROBE.format(port=port))


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
    port, net = net_health(run)
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


#: Шаги стека: примеры, название, функция, нужен ли он следующим шагам. A67 ломает стек — он
#: отдельно и последним: шаги на живом стеке встают до него.
STACK_STEPS: list[tuple[str, str, Callable[[Run], str], bool]] = [
    ("A73 A87", "правила на настоящем docker-compose.yml", step_rules_on_file, False),
    ("A89", "правила на основном файле с боевым наложением", step_rules_on_prod, False),
    ("A65 A63", "подъём стека и migrate", step_up, True),
    ("A69", "здоровье api после старта", step_health, False),
    ("A66", "повтор migrate", step_repeat, False),
    ("A69", "остановка и старт db", step_outage, False),
]
BREAKING_STEPS: list[tuple[str, str, Callable[[Run], str], bool]] = [
    ("A67", "неверный пароль у migrate", step_bad_migrate, False),
]
