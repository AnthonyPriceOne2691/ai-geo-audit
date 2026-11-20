"""Записи ответов внешних команд для подставного исполнителя скрипта выката (Ч1.6а).

«Разведка» — вид сервера по разведке 2026-11-20, собранный тимлидом с заменой имён, портов и
адресов (локальный документ вне git; docker 29.8.1): число контейнеров, проекты, сервисы,
состояния, здоровье, `RestartCount`, виды `StartedAt`, пределы памяти, порты на петле.
«Раннер» — docker 29.9.3 на машине исполнителя, 2026-11-20: JSON-форма ответа шаблона
`INSPECT` — ключи, их порядок, узлы `State`, `Config.Labels`, `HostConfig.PortBindings`; этого
разведка не снимала. Состояние остановленных миграций не снимал никто — оно в обоих видах.
"""

from __future__ import annotations

import hashlib
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
