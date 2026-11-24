"""Шаги оракула стека Ч1.5б — защита стека на поднятом compose: права контейнеров (A87), выход
наружу и сети (A86), адрес клиента api через web и напрямую (A83, первая строка).

Импорт в одну сторону: `stack_oracle.py` ставит эти шаги на живом стеке до шага A67.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from stack_oracle_compose import StepError, compose, inspect
from stack_oracle_web import fetch, web_url

if TYPE_CHECKING:
    from stack_oracle import Run

#: Сервисы стека: у каждого корень только для чтения (A87); у db запись — только в tmpfs (слово
#: владельца 2026-11-23T14:12:15Z).
SERVICES = ("db", "redis", "migrate", "api", "web")
TMPFS = {"db": ["/tmp", "/var/run/postgresql"]}  # noqa: S108 -- tmpfs контейнера, не файл машины
NO_NEW_PRIVILEGES = "no-new-privileges:true"
#: Адреса из диапазона документации (RFC 5737): не ведут ни в одну настоящую сеть.
NOWHERE, SENT = "192.0.2.86:80", "198.51.100.83"
#: Из web — по имени до базы и очереди (A86).
INSIDE = ("db:5432", "redis:6379")
#: Номера запросов шага A83: по ним — свои строки доступа api.
VIA_WEB, DIRECT = "oracle-request-a83-web", "oracle-request-a83-direct"
#: Соединения изнутри контейнера: на каждую цель — цель, итог (`connected`, имя errno или
#: исключения) и секунды.
DIAL = """
import errno, socket, sys, time
for target in sys.argv[2:]:
    host, port = target.rsplit(":", 1)
    started = time.monotonic()
    try:
        socket.create_connection((host, int(port)), timeout=float(sys.argv[1])).close()
        found = "connected"
    except OSError as error:
        found = errno.errorcode.get(error.errno or 0, type(error).__name__)
    print(target, found, f"{time.monotonic() - started:.3f}")
"""
#: Запрос к api напрямую с присланным адресом клиента: код ответа и свой адрес в сети.
ASK = """
import http.client, sys
connection = http.client.HTTPConnection(sys.argv[1], int(sys.argv[2]), timeout=10)
headers = {"X-Forwarded-For": sys.argv[3], "X-Request-ID": sys.argv[4]}
connection.request("GET", "/api/health", headers=headers)
own = connection.sock.getsockname()[0]
print(connection.getresponse().status, own)
"""


def privilege_problems(name: str, host: dict[str, Any]) -> list[str]:
    """A87: `CapDrop` — ровно `["ALL"]`, в `SecurityOpt` — `no-new-privileges:true`, корень —
    только для чтения; у сервиса из `TMPFS` — ровно его пути в `Tmpfs`."""
    problems = [f"{name}: CapDrop {host.get('CapDrop')}"] * (host.get("CapDrop") != ["ALL"])
    options = host.get("SecurityOpt") or []
    problems += [f"{name}: SecurityOpt {options}"] * (NO_NEW_PRIVILEGES not in options)
    root, tmpfs = host.get("ReadonlyRootfs"), sorted(host.get("Tmpfs") or {})
    problems += [f"{name}: ReadonlyRootfs {root}"] * (root is not True)
    return problems + [f"{name}: Tmpfs {tmpfs}"] * (name in TMPFS and tmpfs != TMPFS[name])


def dialed(text: str) -> dict[str, tuple[str, float]]:
    """Итоги `DIAL` по целям; строки другого вида пропускаются."""
    rows = (line.split() for line in text.splitlines())
    return {row[0]: (row[1], float(row[2])) for row in rows if len(row) == 3}


def outward_problems(where: str, found: dict[str, tuple[str, float]]) -> list[str]:
    """A86: из сети данных наружу — `ENETUNREACH` меньше чем за секунду, а не тайм-аут."""
    got = found.get(NOWHERE)
    if got and got[0] == "ENETUNREACH" and got[1] < 1:
        return []
    return [f"{where} → {NOWHERE}: {' за '.join(map(str, got)) + ' с' if got else 'итога нет'}"]


def inside_problems(found: dict[str, tuple[str, float]]) -> list[str]:
    """A86: из web соединения с db и redis нет — имя не распознаётся или соединение не
    устанавливается; цели без итога — красное: судить не по чему."""
    got = {target: found.get(target, ("итога нет", 0.0))[0] for target in INSIDE}
    return [
        f"web → {target}: {kind}"
        for target, kind in got.items()
        if kind in ("connected", "итога нет")
    ]


def network_problems(nets: dict[str, list[str]], internal: dict[str, bool]) -> list[str]:
    """A86: у всех, кроме web, сети только внутренние; обычная сеть — только у web."""
    return [
        f"{name}: обычная сеть {net}"
        for name, found in sorted(nets.items())
        if name != "web"
        for net in found
        if not internal.get(net, False)
    ]


def clients(log: str) -> dict[str, str]:
    """A83: поле `client` строк доступа api (Ч1.3, A47) по номеру запроса."""
    found = {}
    for line in log.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and "client" in record:
            found[str(record.get("request_id"))] = str(record["client"])
    return found


def step_privileges(run: Run) -> str:
    """A87, первая строка: у db, redis, migrate, api, web — `CapDrop` `["ALL"]` и
    `no-new-privileges:true`; корень только для чтения у всех, у db — с `tmpfs`."""
    problems = [
        p for name in SERVICES for p in privilege_problems(name, inspect(run, name)["HostConfig"])
    ]
    if problems:
        raise StepError("; ".join(problems))
    return f"{', '.join(SERVICES)}: CapDrop [ALL], {NO_NEW_PRIVILEGES}; корень — только чтение"


def step_networks(run: Run) -> str:
    """A86: из api и разового migrate наружу — `ENETUNREACH` меньше чем за секунду; из web до db и
    redis по имени соединения нет; обычная сеть — только у web."""
    outward = {
        "api": compose(
            run, "exec", "-T", "api", "python", "-c", DIAL, "5", NOWHERE, limit="docker"
        ),
        "migrate": compose(
            run,
            "run",
            "--rm",
            "-T",
            "--no-deps",
            "--entrypoint",
            "python",
            "migrate",
            "-c",
            DIAL,
            "5",
            NOWHERE,
        ),
    }
    problems = [p for where, text in outward.items() for p in outward_problems(where, dialed(text))]
    web = inspect(run, "web")["Id"]
    probe = f"{run.name}-a86"
    run.docker.containers.append(probe)
    flags = ["--rm", "--name", probe, "--network", f"container:{web}", "--entrypoint", "python"]
    inside = dialed(run.docker("run", *flags, run.image, "-c", DIAL, "2", *INSIDE))
    problems += inside_problems(inside)
    nets = {name: list(inspect(run, name)["NetworkSettings"]["Networks"]) for name in SERVICES}
    names = sorted({net for found in nets.values() for net in found})
    shown = run.docker(
        "network", "inspect", "--format", "{{.Name}} {{.Internal}}", *names, limit="docker"
    )
    internal = {
        row[0]: row[1] == "true" for row in map(str.split, shown.splitlines()) if len(row) == 2
    }
    problems += network_problems(nets, internal)
    if problems:
        raise StepError("; ".join(problems))
    kinds = ", ".join(f"{target} — {inside[target][0]}" for target in INSIDE)
    return f"api и migrate наружу — ENETUNREACH; из web: {kinds}; обычная сеть только у web"


def front_net(run: Run) -> str:
    """Сеть front стека — общая у web и api."""
    web, api = (inspect(run, name)["NetworkSettings"]["Networks"] for name in ("web", "api"))
    shared = [str(name) for name in web if name in api]
    if len(shared) != 1:
        raise StepError(f"общих сетей у web и api {len(shared)}, а не одна front: {shared}")
    return shared[0]


def step_client_address(run: Run) -> str:
    """A83, первая строка: `X-Forwarded-For` с адресом из диапазона документации через web — в
    строке доступа api присланный адрес; тот же запрос напрямую из разового контейнера в сети
    front — адрес контейнера, а не присланный."""
    head = {"X-Forwarded-For": SENT, "X-Request-ID": VIA_WEB}
    code = fetch(web_url(run, "/api/health"), head)[0]
    env = dict(e.split("=", 1) for e in inspect(run, "api")["Config"]["Env"])
    probe = f"{run.name}-a83"
    run.docker.containers.append(probe)
    flags = ["--rm", "--name", probe, "--network", front_net(run), "--entrypoint", "python"]
    port = env.get("API_PORT", "8107")
    answer = run.docker("run", *flags, run.image, "-c", ASK, "api", port, SENT, DIRECT).split()
    found = clients(compose(run, "logs", "--no-color", "--no-log-prefix", "api", limit="docker"))
    own = answer[1] if len(answer) == 2 else "?"
    problems = [f"через web — {code}, напрямую — {' '.join(answer)}"] * (
        code != 200 or answer[:1] != ["200"]
    )
    problems += [f"через web client — {found.get(VIA_WEB)}, а не {SENT}"] * (
        found.get(VIA_WEB) != SENT
    )
    problems += [f"напрямую client — {found.get(DIRECT)}, а не {own}"] * (
        found.get(DIRECT) != own or own == SENT
    )
    if problems:
        raise StepError("; ".join(problems))
    return f"через web client — {SENT}; напрямую из front — {own}, присланный не принят"


#: Шаги защиты стека — на живом стеке, до шага A67.
HARDENING_STEPS: list[tuple[str, str, Callable[[Run], str], bool]] = [
    ("A87", "права и корень контейнеров", step_privileges, False),
    ("A86", "выход наружу и сети", step_networks, False),
    ("A83", "адрес клиента api через web и напрямую", step_client_address, False),
]
