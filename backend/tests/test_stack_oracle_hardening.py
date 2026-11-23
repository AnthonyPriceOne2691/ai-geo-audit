"""Чистые части шагов защиты стека оракула без Docker (A83, A86, A87): права контейнера, итоги
соединений изнутри контейнеров, сети сервисов, поле `client` строк доступа api. Модуль
`scripts/stack_oracle_hardening.py` лежит вне пакета.
"""

from __future__ import annotations

import importlib
import json
import sys

import dbtools
import pytest

sys.path.insert(0, str(dbtools.ROOT / "scripts"))
hard = importlib.import_module("stack_oracle_hardening")

GOOD = {"CapDrop": ["ALL"], "SecurityOpt": ["no-new-privileges:true"], "ReadonlyRootfs": True}
#: `HostConfig.Tmpfs` у db — путь и его опции (A87, первая строка).
DB_TMPFS = {"/var/run/postgresql": "", "/tmp": ""}


@pytest.mark.parametrize(
    ("name", "change", "problem"),
    [
        ("api", {}, ""),
        ("db", {"Tmpfs": DB_TMPFS}, ""),
        ("db", {"Tmpfs": DB_TMPFS, "ReadonlyRootfs": False}, "db: ReadonlyRootfs False"),
        ("db", {}, "db: Tmpfs []"),
        ("db", {"Tmpfs": {"/tmp": ""}}, "db: Tmpfs ['/tmp']"),
        ("api", {"CapDrop": None}, "api: CapDrop None"),
        ("redis", {"CapDrop": ["NET_RAW"]}, "redis: CapDrop ['NET_RAW']"),
        ("web", {"SecurityOpt": None}, "web: SecurityOpt []"),
        ("migrate", {"SecurityOpt": ["label=disable"]}, "migrate: SecurityOpt ['label=disable']"),
        ("web", {"ReadonlyRootfs": False}, "web: ReadonlyRootfs False"),
    ],
)
def test_a87_container_privileges_are_judged_by_host_config(
    name: str, change: dict[str, object], problem: str
) -> None:
    """A87, первая строка: у каждого — `CapDrop` `["ALL"]` и `no-new-privileges:true`; корень
    только для чтения у db, redis, migrate, api и web; у db в `HostConfig.Tmpfs` —
    `/var/run/postgresql` и `/tmp` (слово владельца 2026-11-23T14:12:15Z); иначе — красное с
    именем."""
    assert hard.SERVICES == ("db", "redis", "migrate", "api", "web")
    assert hard.TMPFS == {"db": ["/tmp", "/var/run/postgresql"]}
    found = hard.privilege_problems(name, GOOD | change)
    assert found == ([problem] if problem else [])


def test_a86_dial_lines_are_read_per_target() -> None:
    """A86: итог соединений — по целям: итог и секунды; строки другого вида пропускаются."""
    text = (
        "192.0.2.86:80 ENETUNREACH 0.001\n"
        "Traceback (most recent call last):\n"
        "db:5432 gaierror 0.020\n"
    )
    assert hard.dialed(text) == {
        "192.0.2.86:80": ("ENETUNREACH", 0.001),
        "db:5432": ("gaierror", 0.02),
    }


@pytest.mark.parametrize(
    ("found", "problem"),
    [
        ({"192.0.2.86:80": ("ENETUNREACH", 0.001)}, ""),
        (
            {"192.0.2.86:80": ("TimeoutError", 5.003)},
            "api → 192.0.2.86:80: TimeoutError за 5.003 с",
        ),
        ({"192.0.2.86:80": ("ENETUNREACH", 1.0)}, "api → 192.0.2.86:80: ENETUNREACH за 1.0 с"),
        ({"192.0.2.86:80": ("EHOSTUNREACH", 0.2)}, "api → 192.0.2.86:80: EHOSTUNREACH за 0.2 с"),
        ({}, "api → 192.0.2.86:80: итога нет"),
    ],
    ids=["сети нет", "тайм-аут", "секунда", "хост", "нет итога"],
)
def test_a86_outward_is_enetunreach_in_under_a_second(
    found: dict[str, tuple[str, float]], problem: str
) -> None:
    """A86: адрес из диапазона документации (RFC 5737) из сети данных — `ENETUNREACH` меньше чем за
    секунду; тайм-аут, другая ошибка и пропавший итог — красное с причиной."""
    assert hard.NOWHERE.split(":")[0] == "192.0.2.86"
    assert hard.outward_problems("api", found) == ([problem] if problem else [])


@pytest.mark.parametrize(
    ("db", "redis", "problems"),
    [
        ("gaierror", "gaierror", []),
        ("ECONNREFUSED", "TimeoutError", []),
        ("connected", "gaierror", ["web → db:5432: connected"]),
        (None, "connected", ["web → db:5432: итога нет", "web → redis:6379: connected"]),
    ],
    ids=["имени нет", "соединения нет", "база видна", "без итога"],
)
def test_a86_web_has_no_connection_to_db_and_redis(
    db: str | None, redis: str, problems: list[str]
) -> None:
    """A86: из web до db и redis по имени соединения нет — имя не распознаётся или соединение не
    устанавливается; установленное соединение или цель без итога — красное с её именем."""
    found = {"redis:6379": (redis, 0.01)} | ({"db:5432": (db, 0.01)} if db else {})
    assert hard.inside_problems(found) == problems


def test_a86_only_web_has_an_ordinary_network() -> None:
    """A86: db, redis, migrate и api — только во внутренних сетях; обычная сеть — только у web;
    сеть, о которой докер не сказал, — обычная."""
    nets = {
        "db": ["s_data"],
        "redis": ["s_data"],
        "api": ["s_data", "s_front"],
        "web": ["s_front", "s_edge"],
    }
    internal = {"s_data": True, "s_front": True, "s_edge": False}
    assert hard.network_problems(nets, internal) == []
    assert hard.network_problems(nets | {"redis": ["s_data", "s_edge"]}, internal) == [
        "redis: обычная сеть s_edge"
    ]
    assert hard.network_problems(nets, internal | {"s_data": False}) == [
        "api: обычная сеть s_data",
        "db: обычная сеть s_data",
        "redis: обычная сеть s_data",
    ]
    assert hard.network_problems(nets | {"db": ["s_other"]}, internal) == [
        "db: обычная сеть s_other"
    ]


def test_a83_client_field_is_read_per_request_id() -> None:
    """A83, первая строка: поле `client` строки доступа api — по номеру запроса; строки не JSON,
    не объект и без `client` пропускаются; у повтора номера — последняя строка."""
    lines = [
        json.dumps({"message": "запрос", "request_id": "a", "client": "198.51.100.83"}),
        "uvicorn: не JSON",
        json.dumps(["client"]),
        json.dumps({"message": "старт", "request_id": "b"}),
        json.dumps({"message": "запрос", "request_id": "c", "client": "172.16.0.3"}),
        json.dumps({"message": "запрос", "request_id": "c", "client": "172.16.0.4"}),
    ]
    assert hard.clients("\n".join(lines)) == {"a": "198.51.100.83", "c": "172.16.0.4"}
    assert hard.SENT == "198.51.100.83"
