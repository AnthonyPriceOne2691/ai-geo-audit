"""Правила учёток хранилищ `db-roles` и `redis-auth` (A177, A178) над выводом
`docker compose config --no-interpolate --format json`: роли базы и пароль очереди.

Конфиги — верный стек `test_stack_rules.valid()` с одной порчей. Каждая проба — ровно своё
нарушение: без правила проба проходит, и тест красный. Значений в выводе нет нигде.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest
from test_stack_rules import (
    DB_SCRIPT,
    HEALTH,
    PASS_LINE,
    Found,
    drop,
    env,
    found,
    put,
    run,
    svc,
    valid,
)

# Правила учёток хранилищ (A177, A178): проба каждого условия — ровно её нарушение; без правила
# проба проходит, и тест красный. Значений нет нигде: в выводе — код, сервис и имя переменной.
LITERAL = "literal-pass-12"
API_ADDRESS = "${API_DATABASE_URL:?адрес}"
MIGRATE_ADDRESS = "${MIGRATE_DATABASE_URL:?адрес}"
SCRIPT_SHORT = "./deploy/db/roles.sql:/docker-entrypoint-initdb.d/roles.sql"
DATA = {"type": "volume", "source": "db-data", "target": "/var/lib/data"}


def addresses(value: str, *names: str) -> Callable[[dict[str, Any]], None]:
    """Один и тот же адрес базы у названных процессов."""

    def spoil(config: dict[str, Any]) -> None:
        for name in names:
            config["services"][name]["environment"]["DATABASE_URL"] = value

    return spoil


def volumes(*entries: object) -> Callable[[dict[str, Any]], None]:
    return put(svc("db"), "volumes", list(entries))


def role(var: str | None, service: str = "db") -> Found:
    return {("db-roles", service, var)}


# Проба и ровно её нарушения (A177): код, сервис, переменная.
ROLE_PROBES = [
    ("no-user", drop(env("db"), "POSTGRES_USER"), role("POSTGRES_USER")),
    ("user-empty", put(env("db"), "POSTGRES_USER", ""), role("POSTGRES_USER")),
    ("no-db", drop(env("db"), "POSTGRES_DB"), role("POSTGRES_DB")),
    (
        "no-migrate-password",
        drop(env("db"), "POSTGRES_MIGRATE_PASSWORD"),
        role("POSTGRES_MIGRATE_PASSWORD"),
    ),
    ("no-app-password", drop(env("db"), "POSTGRES_APP_PASSWORD"), role("POSTGRES_APP_PASSWORD")),
    (
        "migrate-password-literal",
        put(env("db"), "POSTGRES_MIGRATE_PASSWORD", LITERAL),
        role("POSTGRES_MIGRATE_PASSWORD"),
    ),
    (
        "app-password-no-reason",
        put(env("db"), "POSTGRES_APP_PASSWORD", "${POSTGRES_APP_PASSWORD}"),
        role("POSTGRES_APP_PASSWORD"),
    ),
    (
        "app-password-default",
        put(env("db"), "POSTGRES_APP_PASSWORD", "${POSTGRES_APP_PASSWORD:-}"),
        role("POSTGRES_APP_PASSWORD"),
    ),
    ("no-script", volumes(DATA), role(None)),
    ("script-read-write", volumes(DATA, {**DB_SCRIPT, "read_only": False}), role(None)),
    ("script-read-write-short", volumes("db-data:/var/lib/data", SCRIPT_SHORT), role(None)),
    ("script-elsewhere", volumes(DATA, {**DB_SCRIPT, "target": "/roles.sql"}), role(None)),
    (
        "script-lookalike",
        volumes(DATA, {**DB_SCRIPT, "target": "/docker-entrypoint-initdb.d-old/roles.sql"}),
        role(None),
    ),
    ("migrate-from-api", addresses(API_ADDRESS, "migrate"), role("DATABASE_URL", "migrate")),
    ("api-from-migrate", addresses(MIGRATE_ADDRESS, "api"), role("DATABASE_URL", "api")),
    (
        "one-for-both",
        addresses("${DATABASE_URL:?адрес}", "migrate", "api"),
        role("DATABASE_URL", "migrate") | role("DATABASE_URL", "api"),
    ),
    (
        "web-postgres-password",
        put(svc("web"), "environment", {"POSTGRES_PASSWORD": "${DB_PASSWORD:?пароль}"}),
        role("POSTGRES_PASSWORD", "web"),
    ),
    (
        "web-migrate-password",
        put(
            svc("web"),
            "environment",
            {"POSTGRES_MIGRATE_PASSWORD": "${POSTGRES_MIGRATE_PASSWORD:?пароль}"},
        ),
        role("POSTGRES_MIGRATE_PASSWORD", "web"),
    ),
    (
        "redis-app-password",
        put(env("redis"), "POSTGRES_APP_PASSWORD", "${POSTGRES_APP_PASSWORD:?пароль}"),
        role("POSTGRES_APP_PASSWORD", "redis"),
    ),
]


@pytest.mark.parametrize(
    ("spoil", "expected"),
    [probe[1:] for probe in ROLE_PROBES],
    ids=[probe[0] for probe in ROLE_PROBES],
)
def test_a177_each_db_roles_condition_has_its_probe(spoil: Callable, expected: Found) -> None:
    """A177: у db — `POSTGRES_USER`, `POSTGRES_DB` и пароли ролей `${VAR:?причина}`; скрипт ролей —
    только для чтения в `/docker-entrypoint-initdb.d/`; адреса процессов — из `MIGRATE_DATABASE_URL`
    и `API_DATABASE_URL`, а не одной переменной на двоих; пароли базы — только у db. Проба — ровно
    её нарушение с кодом `db-roles`, сервисом и переменной."""
    config = valid()
    spoil(config)
    assert found(config) == expected


@pytest.mark.parametrize(
    ("service", "var"),
    [("api", "POSTGRES_PASSWORD"), ("migrate", "POSTGRES_APP_PASSWORD")],
    ids=["api", "migrate"],
)
def test_a177_db_password_at_a_process_is_caught_by_both_rules(service: str, var: str) -> None:
    """A177: у api и migrate чужой пароль базы ловят оба правила — `unknown-var` Ч1.5а и
    `db-roles`."""
    config = valid()
    config["services"][service]["environment"][var] = f"${{{var}:?причина}}"
    assert found(config) == {("unknown-var", service, var), ("db-roles", service, var)}


@pytest.mark.parametrize(
    "change",
    [
        volumes("db-data:/var/lib/data", f"{SCRIPT_SHORT}:ro"),
        volumes(DATA, {**DB_SCRIPT, "source": "/srv/other.sql"}),
        volumes(DATA, f"{SCRIPT_SHORT}:ro,z"),
        addresses("${MIGRATE_DATABASE_URL:?другая причина}", "migrate"),
        put(env("db"), "POSTGRES_PASSWORD", "${DB_PASSWORD:?пароль}"),
    ],
    ids=["short-ro", "other-file", "short-ro-z", "other-reason", "own-password"],
)
def test_a177_neighbour_cases_are_not_violations(change: Callable) -> None:
    """A177, соседние случаи (L53): скрипт короткой записью с `:ro` и с режимами, другой файл,
    другая причина у ссылки, пароль суперпользователя у db — не нарушения."""
    config = valid()
    change(config)
    assert found(config) == set()


def redis_script(first: str, *lines: str) -> Callable[[dict[str, Any]], None]:
    """Команда redis: `sh -c` с here-doc `<<EOF` из строк `lines`."""
    body = "\n".join((f"{first} <<EOF", *lines, "EOF")) + "\n"
    return put(svc("redis"), "command", ["sh", "-c", body])


def health(test: object) -> Callable[[dict[str, Any]], None]:
    return put(svc("redis"), "healthcheck", {"test": test, **HEALTH})


def auth(service: str = "redis", var: str | None = None) -> Found:
    return {("redis-auth", service, var)}


QUEUE_URL = "redis://:" + LITERAL + "@redis:6379/0"
CLI = "REDISCLI_AUTH=$$REDIS_PASSWORD redis-cli ping | grep -x PONG"
FLAG_CLI = CLI.replace("redis-cli ping", "redis-cli -a $$REDIS_PASSWORD ping")
QUEUE_REF = "${REDIS_PASSWORD:?пароль}"
# Проба и ровно её нарушение (A178): код, сервис, переменная.
AUTH_PROBES = [
    ("no-password", drop(env("redis"), "REDIS_PASSWORD"), auth(var="REDIS_PASSWORD")),
    ("password-literal", put(env("redis"), "REDIS_PASSWORD", LITERAL), auth(var="REDIS_PASSWORD")),
    (
        "password-no-reason",
        put(env("redis"), "REDIS_PASSWORD", "${REDIS_PASSWORD}"),
        auth(var="REDIS_PASSWORD"),
    ),
    (
        "password-default",
        put(env("redis"), "REDIS_PASSWORD", "${REDIS_PASSWORD:-x}"),
        auth(var="REDIS_PASSWORD"),
    ),
    ("no-user", drop(svc("redis"), "user"), auth()),
    ("user-root", put(svc("redis"), "user", "root"), auth()),
    ("user-zero", put(svc("redis"), "user", "0"), auth()),
    ("user-uid", put(svc("redis"), "user", "999"), auth()),
    ("user-uid-gid", put(svc("redis"), "user", "999:1000"), auth()),
    ("no-command", drop(svc("redis"), "command"), auth()),
    ("not-sh", put(svc("redis"), "command", ["redis-server", "-"]), auth()),
    ("no-exec", redis_script("redis-server -", PASS_LINE), auth()),
    (
        "requirepass-argument",
        redis_script("exec redis-server - --requirepass $$REDIS_PASSWORD", PASS_LINE),
        auth(),
    ),
    ("no-requirepass", redis_script("exec redis-server -"), auth()),
    ("literal-on-input", redis_script("exec redis-server -", f"requirepass {LITERAL}"), auth()),
    (
        "compose-substitution",
        redis_script("exec redis-server -", "requirepass $REDIS_PASSWORD"),
        auth(),
    ),
    (
        "compose-substitution-braces",
        redis_script("exec redis-server -", "requirepass ${REDIS_PASSWORD}"),
        auth(),
    ),
    ("no-here-doc", put(svc("redis"), "command", ["sh", "-c", "exec redis-server -\n"]), auth()),
    ("health-no-auth", health(["CMD-SHELL", "redis-cli ping | grep -x PONG"]), auth()),
    (
        "health-no-pong",
        health(["CMD-SHELL", "REDISCLI_AUTH=$$REDIS_PASSWORD redis-cli ping"]),
        auth(),
    ),
    ("health-old-form", health(["CMD", "redis-cli", "ping"]), auth()),
    (
        "health-compose-substitution",
        health(["CMD-SHELL", 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli ping | grep -x PONG']),
        auth(),
    ),
    (
        "health-literal",
        health(["CMD-SHELL", f"REDISCLI_AUTH={LITERAL} redis-cli ping | grep -x PONG"]),
        auth(),
    ),
    ("health-a-flag", health(["CMD-SHELL", FLAG_CLI]), auth()),
    (
        "web-a-flag",
        put(svc("web"), "healthcheck", {"test": ["CMD", "redis-cli", "-h", "redis", "-a", "x"]}),
        auth("web"),
    ),
    (
        "api-u-flag",
        put(svc("api"), "command", ["redis-cli", "-u", QUEUE_URL, "ping"]),
        auth("api"),
    ),
    (
        "migrate-pass-flag",
        put(svc("migrate"), "command", ["sh", "-c", "redis-cli --pass x -h redis ping"]),
        auth("migrate"),
    ),
    ("db-password", put(env("db"), "REDIS_PASSWORD", QUEUE_REF), auth("db", "REDIS_PASSWORD")),
    (
        "web-password",
        put(svc("web"), "environment", {"REDIS_PASSWORD": QUEUE_REF}),
        auth("web", "REDIS_PASSWORD"),
    ),
]


@pytest.mark.parametrize(
    ("spoil", "expected"),
    [probe[1:] for probe in AUTH_PROBES],
    ids=[probe[0] for probe in AUTH_PROBES],
)
def test_a178_each_redis_auth_condition_has_its_probe(spoil: Callable, expected: Found) -> None:
    """A178: у redis пароль очереди — `${VAR:?причина}`, `user: redis` по имени, команда — `sh -c`
    с `exec redis-server -` и `requirepass $$REDIS_PASSWORD` на входе, проверка здоровья — с
    `REDISCLI_AUTH` и сверкой с `PONG`; один `$` — подстановка compose; флага с паролем нет ни у
    кого; `REDIS_PASSWORD` — только у redis. Проба — ровно её нарушение с кодом `redis-auth`."""
    config = valid()
    spoil(config)
    assert found(config) == expected


@pytest.mark.parametrize("service", ["api", "migrate"])
def test_a178_queue_password_at_a_process_is_caught_by_both_rules(service: str) -> None:
    """A178: `REDIS_PASSWORD` у api и migrate ловят оба правила — `unknown-var` Ч1.5а и
    `redis-auth`."""
    config = valid()
    config["services"][service]["environment"]["REDIS_PASSWORD"] = QUEUE_REF
    var = "REDIS_PASSWORD"
    assert found(config) == {("unknown-var", service, var), ("redis-auth", service, var)}


@pytest.mark.parametrize(
    "change",
    [
        redis_script(
            "exec redis-server - --maxmemory 100mb --maxmemory-policy noeviction", PASS_LINE
        ),
        put(svc("redis"), "command", f'sh -c "exec redis-server - <<EOF\n{PASS_LINE}\nEOF\n"'),
        health(["CMD-SHELL", CLI.replace("grep -x", "grep -q")]),
        health(f"CMD-SHELL {CLI}"),
        put(
            svc("web"),
            "healthcheck",
            {"test": ["CMD", "redis-cli", "-u", "redis://redis:6379/0", "ping"]},
        ),
        put(svc("web"), "healthcheck", {"test": ["CMD", "grep", "-a", "x", "/dev/null"]}),
    ],
    ids=["flags", "command-string", "grep-q", "test-string", "u-no-password", "a-no-cli"],
)
def test_a178_neighbour_cases_are_not_violations(change: Callable) -> None:
    """A178, соседние случаи (L53): флаги наложения после `-`, команда строкой, `grep -q`, проверка
    здоровья строкой, `-u` с адресом без пароля, `-a` без redis-cli — не нарушения."""
    config = valid()
    change(config)
    assert found(config) == set()


def test_a177_a178_lines_name_service_and_variable_without_values(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A177, A178: строка нарушения — код, сервис и переменная; значений нет: литерал пароля роли
    и литерал в `requirepass` в вывод не попадают."""
    config = valid()
    put(env("db"), "POSTGRES_APP_PASSWORD", LITERAL)(config)
    redis_script("exec redis-server -", f"requirepass {LITERAL}")(config)
    code, out = run(monkeypatch, capsys, json.dumps(config))
    assert code == 1
    assert "db-roles: сервис db, переменная POSTGRES_APP_PASSWORD" in out
    assert "redis-auth: сервис redis —" in out
    assert LITERAL not in out
