"""Записи ответов внешних команд для подставного исполнителя скрипта выката (Ч1.6а).

«Разведка» — вид сервера по разведке 2026-11-20, собранный тимлидом с заменой имён, портов и
адресов (локальный документ вне git; docker 29.8.1): число контейнеров, проекты, сервисы,
состояния, здоровье, `RestartCount`, виды `StartedAt`, пределы памяти, порты на петле.
«Раннер» — docker 29.9.3 на машине исполнителя, 2026-11-20: JSON-форма ответа шаблона
`INSPECT` — ключи, их порядок, узлы `State`, `Config.Labels`, `HostConfig.PortBindings`; этого
разведка не снимала. Состояние остановленных миграций не снимал никто — оно в обоих видах.
Для `deploy.py check` (6а-3): строки `ss`, маршрутов и файлов compose соседа — разведка, блоки
8, 9 и 11 дословно с заменой; сети, тома и `docker compose config` — раннер, 2026-11-27 (docker
29.9.3, compose 5.7.1; на сервере — 29.8.1 и 5.5.1, сверено ведущей 2026-11-27).
"""

from __future__ import annotations

import hashlib
import ipaddress
import json

#: Разведка: дробная часть `StartedAt` — 6–9 знаков без хвостовых нулей; дни старта — разные.
FRACTIONS = (".152063244", ".09224642", ".610123")
DAYS = ("2026-10-09", "2026-10-30", "2026-11-12", "2026-11-18", "2026-11-19")
#: Разведка, блок 8: сервисы проекта соседа — (имя, контейнеров, предел памяти в байтах).
ALPHA = [("scaled", 16, 1610612736), *((f"svc-{s}", 1, 1610612736) for s in "abcd")]
ALPHA += [("svc-e", 1, 3221225472), ("svc-f", 1, 268435456), ("svc-g", 1, 268435456)]
ALPHA += [("api", 1, 1073741824), ("web", 1, 268435456), ("postgres", 1, 4294967296)]
ALPHA += [("redis", 1, 536870912)]
BETA = [(service, 1, 0) for service in ("web", "api", "svc-a", "svc-b", "postgres", "redis")]


def cid(name: str) -> str:
    """Id контейнера — 64 знака hex, как у `docker ps --no-trunc`; выдуман по имени."""
    return hashlib.sha256(name.encode()).hexdigest()


def inspect_line(
    name: str,
    project: str,
    *,
    status: str = "running",
    health: str | None = "healthy",
    started: str = "2026-11-18T09:12:09.152063244Z",
    memory: int = 0,
    ports: dict[str, list[dict[str, str]]] | None = None,
) -> str:
    """Строка ответа `docker container inspect --format <INSPECT>` — форма раннера: без пробелов,
    у узлов-карт ключи по алфавиту; `health=None` — узла `Health` нет."""
    running = status == "running"
    state: dict[str, object] = {"Dead": False, "Error": "", "ExitCode": 0, "OOMKilled": False}
    state |= {"FinishedAt": "0001-01-01T00:00:00Z" if running else "2026-11-18T09:12:40.512083117Z"}
    state |= {"Paused": False, "Pid": 4242 if running else 0, "Restarting": False}
    state |= {"Running": running, "StartedAt": started, "Status": status}
    if health:
        state["Health"] = {"FailingStreak": 0, "Log": [], "Status": health}
    service = name.removeprefix(f"{project}-").rsplit("-", 1)[0]
    labels = {"com.docker.compose.oneoff": "False", "com.docker.compose.project": project}
    labels |= {"com.docker.compose.service": service}
    node = {"id": cid(name), "name": "/" + name, "labels": labels if project else {}}
    node |= {"state": dict(sorted(state.items())), "restarts": 0, "memory": memory}
    return json.dumps(node | {"ports": ports or {}}, separators=(",", ":"))


def vary(line: str, **changes: object) -> str:
    """Та же запись с изменёнными полями: `started`, `restarts`, `health`, `id`."""
    raw = json.loads(line)
    paths = {"started": ("state", "StartedAt"), "health": ("state", "Health", "Status")}
    for change, value in changes.items():
        *parents, last = paths.get(change, (change,))
        node = raw
        for part in parents:
            node = node[part]
        node[last] = value
    return json.dumps(raw, separators=(",", ":"))


def loopback(host_port: str) -> dict[str, list[dict[str, str]]]:
    """Разведка: наружу опубликован только веб соседа, `127.0.0.1:P->80/tcp`; форма — раннер."""
    return {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": host_port}]}


def server_machine(
    alpha: str = "nb-alpha", beta: str = "nb-beta", ports: tuple[str, str] = ("9101", "9102")
) -> list[str]:
    """Сервер до выката (разведка, блок 8): 35 контейнеров двух проектов соседа — 33 запущены, у
    всех проверка здоровья и `RestartCount` 0; разовые миграции остановлены."""
    lines: list[str] = []
    for project, services, port in ((alpha, ALPHA, ports[0]), (beta, BETA, ports[1])):
        for service, count, memory in services:
            for replica in range(1, count + 1):
                at = len(lines)
                started = f"{DAYS[at % 5]}T09:12:{at:02d}{FRACTIONS[at % 3]}Z"
                web = loopback(port) if service == "web" else None
                name = f"{project}-{service}-{replica}"
                lines.append(inspect_line(name, project, started=started, memory=memory, ports=web))
        # Состояние остановленной миграции не снималось: у одной узел здоровья есть, у другой нет.
        health = "unhealthy" if project == beta else None
        lines.append(inspect_line(f"{project}-migrate-1", project, status="exited", health=health))
    return lines


def ours() -> list[str]:
    """Наш стек после выката — в снимок соседа не входит (форма — раннер)."""
    return [inspect_line(f"ai-geo-audit-{s}-1", "ai-geo-audit") for s in ("api", "db")]


def listening(ports: tuple[str, str] = ("9101", "9102")) -> str:
    """Разведка, блок 8 (`ss -ltnH`): девять строк дословно, порты соседа заменены; ширина колонок
    плавает — разбирать по пробелам. Видов `*:P` и `[::1]:P` на сервере нет — их держат тесты."""
    rows = (
        "LISTEN 0      4096      127.0.0.54:53   0.0.0.0:*",
        "LISTEN 0      511          0.0.0.0:443  0.0.0.0:*",
        "LISTEN 0      4096         0.0.0.0:22   0.0.0.0:*",
        "LISTEN 0      511          0.0.0.0:80   0.0.0.0:*",
        "LISTEN 0      4096   127.0.0.53%lo:53   0.0.0.0:*",
        f"LISTEN 0      4096       127.0.0.1:{ports[1]} 0.0.0.0:*",
        f"LISTEN 0      4096       127.0.0.1:{ports[0]} 0.0.0.0:*",
        "LISTEN 0      4096            [::]:22      [::]:*",
        "LISTEN 0      511             [::]:80      [::]:*",
    )
    return "".join(f"{row}\n" for row in rows)


def compose_lines(
    alpha: str = "nb-alpha", beta: str = "nb-beta", ports: tuple[str, str] = ("9101", "9180")
) -> str:
    """Разведка, блок 11: строки портов файлов compose соседа на глубине 2 под `/srv` — вывод
    `find … -exec awk` разведки, блок 12 («путь: строка»); отступы и кавычки дословные, порты
    заменены. Объявленное умолчание у второго проекта не равно живому порту (`ss`)."""

    def web(port: str) -> str:
        return "${WEB_PORT:-" + port + "}"

    rows = [
        (alpha, "", f'      - "127.0.0.1:{web(ports[0])}:80"'),
        (alpha, ".dev", '    ports: ["5544:5432"]'),
        (alpha, ".dev", '    ports: ["6490:6379"]'),
        (beta, "", '      - "${WEB_BIND:-127.0.0.1}:' + web(ports[1]) + ':80"'),
        *((beta, ".dev", f'      - "{port}:{port}"') for port in ("5432", "6379", "8000", "5173")),
    ]
    return "".join(f"/srv/{name}/docker-compose{kind}.yml: {line}\n" for name, kind, line in rows)


def routes(subnets: tuple[str, str] = ("172.28.0.0/16", "172.29.0.0/16")) -> str:
    """Разведка, блок 9 (`ip route show`): четыре строки, в конце каждой — пробел; адреса и мосты
    заменены."""
    rows = [
        "default via 203.0.113.1 dev enp1s0 proto static onlink ",
        "172.17.0.0/16 dev docker0 proto kernel scope link src 172.17.0.1 linkdown ",
    ]
    for subnet, bridge in zip(subnets, ("br-0123456789ab", "br-ba9876543210"), strict=True):
        gateway = next(ipaddress.ip_network(subnet).hosts())
        rows.append(f"{subnet} dev {bridge} proto kernel scope link src {gateway} ")
    return "".join(f"{row}\n" for row in rows)


#: КиБ в ГиБ: `/proc/meminfo` считает в kB.
GIB_KB = 1 << 20


def meminfo(available_kb: int = 50 * GIB_KB) -> str:
    """`/proc/meminfo` — вид ядра Linux; числа выдуманы в пропорциях разведки, блок 12: доступно
    (`MemAvailable`) кратно больше свободного — кэш страниц."""
    rows = [("MemTotal", 64 * GIB_KB), ("MemFree", 14 * GIB_KB), ("MemAvailable", available_kb)]
    rows += [("Buffers", GIB_KB), ("Cached", 38 * GIB_KB), ("SwapTotal", 30 * GIB_KB)]
    return "".join(f"{name + ':':<15}{size:>9} kB\n" for name, size in rows)


#: Разведка, блок 10: `docker info --format '{{.DockerRootDir}}'` — каталог стандартный.
DOCKER_ROOT = "/var/lib/docker\n"


def network_line(name: str, project: str = "", subnet: str | None = None) -> str:
    """Строка ответа `docker network inspect --format <NETWORK_FORMAT>` — форма раннера: у сетей
    проекта compose — его метки по алфавиту; `ipam` — `null` у `host` и `none`."""
    labels: dict[str, str] = {}
    if project:
        labels = {
            "com.docker.compose.config-hash": cid(f"hash:{name}"),
            "com.docker.compose.network": name.partition("_")[2],
            "com.docker.compose.project": project,
            "com.docker.compose.version": "5.7.0",
        }
    ipam = None
    if subnet:
        ipam = [{"Subnet": subnet, "Gateway": str(next(ipaddress.ip_network(subnet).hosts()))}]
    node = {"id": cid(f"network:{name}"), "name": name, "labels": labels, "ipam": ipam}
    return json.dumps(node, separators=(",", ":"))


def networks(
    alpha: str = "nb-alpha",
    beta: str = "nb-beta",
    subnets: tuple[str, str] = ("172.28.0.0/16", "172.29.0.0/16"),
) -> list[str]:
    """Разведка, блоки 6–7: пять сетей — `bridge`, `host`, `none` и по сети на проект соседа, у
    каждой не больше одной подсети IPv4."""
    return [
        network_line("bridge", subnet="172.17.0.0/16"),
        network_line("host"),
        network_line("none"),
        network_line(f"{alpha}_default", alpha, subnets[0]),
        network_line(f"{beta}_default", beta, subnets[1]),
    ]


def volume_line(name: str, project: str = "") -> str:
    """Строка ответа `docker volume ls --format <VOLUME_FORMAT>` — форма раннера: метки проекта
    нет — пустая строка. Томов с нашим именем на сервере нет (разведка, блок 5)."""
    return json.dumps({"name": name, "project": project}, separators=(",", ":"))


#: Пределы памяти боевого наложения — как в `docker-compose.prod.yml` (тест сверяет с файлом).
LIMITS: dict[str, str | None] = {
    "api": "512m",
    "db": "1g",
    "migrate": "256m",
    "redis": "256m",
    "web": "128m",
}


def compose_config(limits: dict[str, str | None] | None = None) -> str:
    """Ответ `docker compose … config --no-interpolate --format json` на наши файлы — форма
    раннера: многострочный JSON с отступом 2; `mem_limit` — строка, как в файле; у сетей и тома
    — имена с именем проекта. Прочие ключи ответа проверке не нужны и опущены; `None` — у
    сервиса нет предела."""
    services = {
        name: {} if size is None else {"mem_limit": size}
        for name, size in (LIMITS if limits is None else limits).items()
    }
    nets = {key: {"name": f"ai-geo-audit_{key}"} for key in ("data", "edge", "front")}
    volumes = {"db-data": {"name": "ai-geo-audit_db-data"}}
    config = {"name": "ai-geo-audit", "networks": nets, "services": services, "volumes": volumes}
    return json.dumps(config, indent=2) + "\n"
