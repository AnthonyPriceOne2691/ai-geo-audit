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

from ai_geo_audit.ops import stack_rules as sr

# Правила учёток хранилищ (A177, A178): проба каждого условия — ровно её нарушение; без правила
# проба проходит, и тест красный. Значений нет нигде: в выводе — код, сервис и имя переменной.
LITERAL = "literal-pass-12"
API_ADDRESS = "${API_DATABASE_URL:?адрес}"
MIGRATE_ADDRESS = "${MIGRATE_DATABASE_URL:?адрес}"
SCRIPT_SHORT = "./deploy/db/roles.sql:/docker-entrypoint-initdb.d/roles.sql"
SCRIPT_REF = "${ROLES_DIR:-./deploy/db}/roles.sql:/docker-entrypoint-initdb.d/roles.sql:ro"
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
    (
        "script-other-target",
        volumes(DATA, {**DB_SCRIPT, "target": "/docker-entrypoint-initdb.d/other.sql"}),
        role(None),
    ),
    ("script-other-source", volumes(DATA, {**DB_SCRIPT, "source": "/srv/other.sql"}), role(None)),
    (
        "script-other-short",
        volumes("db-data:/var/lib/data", "./other.sql:/docker-entrypoint-initdb.d/roles.sql:ro"),
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
        volumes(DATA, {**DB_SCRIPT, "source": "/srv/db/roles.sql"}),
        volumes(DATA, f"{SCRIPT_SHORT}:ro,z"),
        volumes("/var/lib/data", f"{SCRIPT_SHORT}:ro"),
        volumes(DATA, SCRIPT_REF),
        volumes(DATA, {**DB_SCRIPT, "source": "roles.sql"}),
        addresses("${MIGRATE_DATABASE_URL:?другая причина}", "migrate"),
        put(env("db"), "POSTGRES_PASSWORD", "${DB_PASSWORD:?пароль}"),
    ],
    ids=[
        "short-ro",
        "other-file",
        "short-ro-z",
        "short-anonymous",
        "short-ref-colon",
        "source-no-dir",
        "other-reason",
        "own-password",
    ],
)
def test_a177_neighbour_cases_are_not_violations(change: Callable) -> None:
    """A177, соседние случаи (L53): скрипт короткой записью с `:ro` и с режимами, рядом
    безымянный том одной частью, двоеточие внутри `${…}` источника — не разделитель, источник без
    каталога, другой файл, другая причина у ссылки, пароль суперпользователя у db — не нарушения."""
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
PASS_DOC = f"exec redis-server - <<EOF\n{PASS_LINE}\nEOF"
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
    (
        "requirepass-after-delimiter",
        put(
            svc("redis"),
            "command",
            ["sh", "-c", f"exec redis-server - <<EOF\nport 6379\nEOF\n{PASS_LINE}\n"],
        ),
        auth(),
    ),
    ("extra-line-on-input", redis_script("exec redis-server -", "port 6379", PASS_LINE), auth()),
    (
        "nopass-user-on-input",
        redis_script("exec redis-server -", "user default on nopass ~* +@all", PASS_LINE),
        auth(),
    ),
    (
        "no-delimiter",
        put(svc("redis"), "command", ["sh", "-c", f"exec redis-server - <<EOF\n{PASS_LINE}\n"]),
        auth(),
    ),
    (
        "text-after-delimiter",
        put(
            svc("redis"),
            "command",
            ["sh", "-c", f"exec redis-server - <<EOF\n{PASS_LINE}\nEOF\necho ok\n"],
        ),
        auth(),
    ),
    (
        "here-doc-quoted",
        put(
            svc("redis"),
            "command",
            ["sh", "-c", f"exec redis-server - <<'EOF'\n{PASS_LINE}\nEOF\n"],
        ),
        auth(),
    ),
    (
        "requirepass-argument-equals",
        redis_script("exec redis-server - --requirepass=$$REDIS_PASSWORD", PASS_LINE),
        auth(),
    ),
    (
        "sh-c-extra-argument",
        put(svc("redis"), "command", ["sh", "-c", f"{PASS_DOC}\n", "redis"]),
        auth(),
    ),
    (
        "here-doc-tab-delimiter",
        put(
            svc("redis"),
            "command",
            ["sh", "-c", f"exec redis-server - <<EOF\n{PASS_LINE}\n\tEOF\n"],
        ),
        auth(),
    ),
    (
        "here-doc-dash-space-delimiter",
        put(
            svc("redis"),
            "command",
            ["sh", "-c", f"exec redis-server - <<-EOF\n\t{PASS_LINE}\n  EOF\n"],
        ),
        auth(),
    ),
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
        "health-compose-substitution-braces",
        health(["CMD-SHELL", 'REDISCLI_AUTH="${REDIS_PASSWORD}" redis-cli ping | grep -x PONG']),
        auth(),
    ),
    (
        "health-single-quotes",
        health(["CMD-SHELL", "REDISCLI_AUTH='$$REDIS_PASSWORD' redis-cli ping | grep -x PONG"]),
        auth(),
    ),
    *[
        (f"health-{name}", health(["CMD-SHELL", text]), auth())
        for name, text in (
            ("or-true", f"{CLI} || true"),
            ("and-true", f"{CLI} && true"),
            ("grep-v", CLI.replace("grep -x", "grep -v")),
            ("grep-c", CLI.replace("grep -x", "grep -c")),
            ("echo-pong", "REDISCLI_AUTH=$$REDIS_PASSWORD redis-cli ping; echo PONG | grep PONG"),
            ("true-first", "REDISCLI_AUTH=$$REDIS_PASSWORD true; redis-cli ping | grep -x PONG"),
            ("only-grep", "REDISCLI_AUTH=$$REDIS_PASSWORD redis-cli ping | grep -x"),
            ("only-pong", "REDISCLI_AUTH=$$REDIS_PASSWORD redis-cli ping | cat PONG"),
            ("other-host", CLI.replace("redis-cli ping", "redis-cli -h redis ping")),
        )
    ],
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
    (
        "web-path-redis-cli",
        put(
            svc("web"),
            "healthcheck",
            {"test": ["CMD", "/usr/local/bin/redis-cli", "-h", "redis", "-a", "x"]},
        ),
        auth("web"),
    ),
    (
        "web-auth-command",
        put(svc("web"), "healthcheck", {"test": ["CMD", "redis-cli", "AUTH", "x"]}),
        auth("web"),
    ),
    (
        "api-pass-equals",
        put(svc("api"), "command", ["redis-cli", "--pass=x", "ping"]),
        auth("api"),
    ),
    ("api-a-flag-string", put(svc("api"), "command", "redis-cli -a x ping"), auth("api")),
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
        put(
            svc("redis"),
            "command",
            ["sh", "-c", f"exec redis-server - <<-EOF\n\t{PASS_LINE}\n\tEOF\n"],
        ),
        put(svc("redis"), "command", f'sh -c "exec redis-server - <<EOF\n{PASS_LINE}\nEOF\n"'),
        health(["CMD-SHELL", CLI.replace("grep -x", "grep -q")]),
        health(CLI),
        put(
            svc("web"),
            "healthcheck",
            {"test": ["CMD", "redis-cli", "-u", "redis://redis:6379/0", "ping"]},
        ),
        put(svc("web"), "healthcheck", {"test": ["CMD", "grep", "-a", "x", "/dev/null"]}),
        put(
            svc("redis"),
            "command",
            ["sh", "-c", f"exec redis-server - <<-XEOF\n\t{PASS_LINE}\n\tXEOF\n"],
        ),
        put(
            svc("web"),
            "healthcheck",
            {"test": ["CMD", "redis-cli", "-u", "redis://[::1", "ping"]},
        ),
    ],
    ids=[
        "flags",
        "here-doc-dash",
        "command-string",
        "grep-q",
        "test-string",
        "u-no-password",
        "a-no-cli",
        "here-doc-dash-x-word",
        "u-unparsable",
    ],
)
def test_a178_neighbour_cases_are_not_violations(change: Callable) -> None:
    """A178, соседние случаи (L53): флаги наложения после `-`, команда строкой, `grep -q`, проверка
    здоровья строкой, `-u` с адресом без пароля и с адресом, который не разобрать, `-a` без
    redis-cli, `<<-` снимает у ограничителя только табуляции — и слово на «X» цело, — не
    нарушения."""
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


ROLE_SPOILS = {name: spoil for name, spoil, _ in ROLE_PROBES}
AUTH_SPOILS = {name: spoil for name, spoil, _ in AUTH_PROBES}
AT = "/docker-entrypoint-initdb.d/roles.sql"
# Причина целиком — по пробе на каждую причину `db-roles` и `redis-auth`.
WHY = [
    (ROLE_SPOILS["no-user"], "не задана явно"),
    (ROLE_SPOILS["migrate-password-literal"], "пароль роли — не ${VAR:?причина}"),
    (ROLE_SPOILS["no-script"], f"скрипт ролей не смонтирован: {AT}"),
    (ROLE_SPOILS["script-read-write-short"], f"скрипт ролей смонтирован на запись: {AT}"),
    (ROLE_SPOILS["migrate-from-api"], "адрес базы — не из MIGRATE_DATABASE_URL"),
    (ROLE_SPOILS["web-postgres-password"], "пароль базы — только у db"),
    (AUTH_SPOILS["password-literal"], "пароль очереди — не ${VAR:?причина}"),
    (AUTH_SPOILS["no-user"], "нет user: redis"),
    (AUTH_SPOILS["no-exec"], "команда — не sh -c с exec redis-server - и requirepass на входе"),
    (AUTH_SPOILS["health-no-auth"], "проверка здоровья — без REDISCLI_AUTH или сверки с PONG"),
    (AUTH_SPOILS["web-password"], "пароль очереди — только у redis"),
    (AUTH_SPOILS["web-a-flag"], "пароль флагом redis-cli виден в списке процессов"),
]


@pytest.mark.parametrize(("spoil", "why"), WHY, ids=[why[:30] for _, why in WHY])
def test_a177_a178_each_reason_is_exact(spoil: Callable, why: str) -> None:
    """A177, A178: причина каждого нарушения `db-roles` и `redis-auth` сверяется целиком — регистр
    и лишние знаки тест видит; код, сервис и переменная — в таблицах проб."""
    config = valid()
    spoil(config)
    assert [violation.why for violation in sr.check(config)] == [why]


@pytest.mark.parametrize(
    "check",
    [
        {"test": ["NONE"], **HEALTH},
        {"test": ["CMD", "NONE"], **HEALTH},
        {"disable": True, "test": ["CMD-SHELL", "redis-cli ping"], **HEALTH},
    ],
    ids=["none", "cmd-none", "disabled-over-test"],
)
def test_a178_health_off_is_judged_by_the_healthcheck_rule(check: dict[str, Any]) -> None:
    """A178: проверки здоровья у redis по сути нет — `NONE`, `CMD NONE` или `disable` поверх
    команды (так compose 5.7.1 пишет наложение, проба 2026-11-26T08:16:41Z) — нарушение
    `healthcheck` Ч1.5а; `redis-auth` её не судит."""
    config = valid()
    put(svc("redis"), "healthcheck", check)(config)
    assert found(config) == {("healthcheck", "redis", None)}
