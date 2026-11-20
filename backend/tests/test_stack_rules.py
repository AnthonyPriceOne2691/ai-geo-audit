"""Правила стека над выводом `docker compose config --no-interpolate --format json` (A71–A73).

Конфиги вымышленные и в той форме, что дал compose 5.7.0 (замер среза 0 в decisions): порты —
объектами или, если в списке есть `${…}`, сырыми строками; `environment` — отображением или
списком. Каждая проба — ровно своё нарушение: без правила проба проходит, и тест красный.
"""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import Field, SecretStr

from ai_geo_audit import config
from ai_geo_audit.config import startup_checks
from ai_geo_audit.ops import stack_rules as sr

DIGEST = "@sha256:" + "0" * 63 + "1"
BACKEND = "ai-geo-audit-backend:local"
HEALTH = {"interval": "5s", "timeout": "3s", "retries": 12, "start_period": "10s"}
CODES = (
    "stack-name",
    "ports",
    "healthcheck",
    "restart",
    "order",
    "init",
    "db-health-tcp",
    "image-digest",
    "trusted-proxy",
    "workdir-volume",
    "required-vars",
    "secret-form",
    "foreign-secret",
    "unknown-var",
    "env-file",
    "master-key",
)

Found = set[tuple[str, str | None, str | None]]


def valid() -> dict[str, Any]:
    """Верный стек: db, redis, migrate, api; секреты — только `${VAR:?причина}`."""
    return {
        "name": "ai-geo-audit",
        "services": {
            "db": {
                "image": "postgres:16-alpine" + DIGEST,
                "restart": "unless-stopped",
                "environment": {"POSTGRES_PASSWORD": "${DB_PASSWORD:?пароль базы}"},
                "healthcheck": {"test": ["CMD-SHELL", "pg_isready -h 127.0.0.1 -U a"], **HEALTH},
                "volumes": [{"type": "volume", "source": "db-data", "target": "/var/lib/data"}],
            },
            "redis": {
                "image": "redis:7.4-alpine" + DIGEST,
                "restart": "unless-stopped",
                "healthcheck": {"test": ["CMD", "redis-cli", "ping"], **HEALTH},
            },
            "migrate": {
                "build": {"context": "/srv/copy", "dockerfile": "deploy/backend/Dockerfile"},
                "image": BACKEND,
                "command": ["alembic", "upgrade", "head"],
                "restart": "no",
                "init": True,
                "depends_on": {"db": {"condition": "service_healthy", "required": True}},
                "environment": {
                    "APP_ENV": "prod",
                    "DATABASE_URL": "${MIGRATE_DATABASE_URL:?адрес базы}",
                },
            },
            "api": {
                "image": BACKEND,
                "restart": "unless-stopped",
                "init": True,
                "depends_on": {
                    "migrate": {"condition": "service_completed_successfully", "required": True},
                    "redis": {"condition": "service_healthy", "required": True},
                },
                "environment": {
                    "APP_ENV": "prod",
                    "DATABASE_URL": "${API_DATABASE_URL:?адрес базы}",
                    "REDIS_URL": "redis://redis:6379/0",
                    "JWT_SECRET": "${JWT_SECRET:?секрет подписи}",
                    "KEYS_MASTER_KEY_FILE": "/run/secrets/master_key",
                    "API_TRUSTED_PROXIES": "${WEB_ADDRESS:?адрес web}",
                },
                "secrets": [{"source": "master_key", "target": "/run/secrets/master_key"}],
                "healthcheck": {"test": ["CMD", "python", "-m", "ai_geo_audit.api.probe"]},
            },
        },
        "secrets": {"master_key": {"file": "/srv/copy/${KEY_FILE:?файл ключа}"}},
    }


def found(config: dict[str, Any]) -> Found:
    return {(v.code, v.service, v.variable) for v in sr.check(config)}


def run(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], text: str):
    """Вход модуля как у оракула: JSON на stdin; код выхода и вывод."""
    monkeypatch.setattr("sys.stdin", io.StringIO(text))
    code = sr.main()
    return code, capsys.readouterr().out


def svc(name: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    return lambda config: config["services"][name]


def env(name: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    return lambda config: config["services"][name]["environment"]


def put(where: Callable, key: str, value: object) -> Callable[[dict[str, Any]], None]:
    return lambda config: where(config).__setitem__(key, value)


def drop(where: Callable, key: str) -> Callable[[dict[str, Any]], None]:
    return lambda config: where(config).pop(key)


def top(config: dict[str, Any]) -> dict[str, Any]:
    return config


def services(config: dict[str, Any]) -> dict[str, Any]:
    return config["services"]


WEB = {
    "build": {"context": "/srv/copy", "dockerfile": "deploy/web/Dockerfile"},
    "image": "ai-geo-audit-web:local",
    "restart": "unless-stopped",
    "healthcheck": {"test": ["CMD", "true"]},
}
WEB_OPEN = {**WEB, "ports": [{"published": "8080", "target": 80}]}
PORT = [{"host_ip": "127.0.0.1", "published": "8080", "target": 80}]
PG_NO_HOST = {"test": ["CMD-SHELL", "pg_isready -U a"]}
PG_SOCKET = {"test": "pg_isready -h /var/run/postgresql"}
BIND_APP = [{"type": "bind", "source": "/srv/src", "target": "/app"}]
DB_LITERAL = "postgresql+asyncpg://a:literal-pass-12@db:5432/a"
ENV_FILE = [{"path": "/srv/copy/.env", "required": True}]
KEY_MOUNT = [{"source": "master_key", "target": "/run/secrets/k"}]
APP_CONFIG = [{"source": "app_env", "target": "/app/.env"}]
KEY_IN_APP = [{"source": "master_key", "target": "/app/key"}]
#: Выключенная проверка с командой: `disable` сильнее `test` (порча M3 проверяющего).
OFF = {"disable": True, "test": ["CMD", "true"]}

# Проба, код, сервис, переменная: ожидается ровно это нарушение.
STRUCTURE = [
    (drop(top, "name"), "stack-name", None, None),
    (put(top, "name", "ch1-05a"), "stack-name", None, None),
    (put(svc("db"), "ports", PORT), "ports", "db", None),
    (put(svc("redis"), "ports", ["6379:6379"]), "ports", "redis", None),
    (put(svc("migrate"), "ports", PORT), "ports", "migrate", None),
    (put(svc("api"), "ports", PORT), "ports", "api", None),
    (put(services, "web", WEB_OPEN), "ports", "web", None),
    (put(svc("redis"), "network_mode", "host"), "ports", "redis", None),
    (drop(svc("redis"), "healthcheck"), "healthcheck", "redis", None),
    (put(svc("api"), "healthcheck", OFF), "healthcheck", "api", None),
    (put(svc("migrate"), "restart", "on-failure"), "restart", "migrate", None),
    (put(svc("redis"), "restart", "always"), "restart", "redis", None),
    (
        put(svc("api"), "depends_on", {"migrate": {"condition": "service_started"}}),
        "order",
        "api",
        None,
    ),
    (put(svc("migrate"), "depends_on", ["db"]), "order", "migrate", None),
    (drop(svc("migrate"), "init"), "init", "migrate", None),
    (put(svc("api"), "init", False), "init", "api", None),
    (put(svc("db"), "healthcheck", PG_NO_HOST), "db-health-tcp", "db", None),
    (put(svc("db"), "healthcheck", PG_SOCKET), "db-health-tcp", "db", None),
    (put(svc("db"), "image", "postgres:16-alpine"), "image-digest", "db", None),
    (put(svc("redis"), "image", "redis:7.2-alpine" + DIGEST), "image-digest", "redis", None),
    (put(svc("redis"), "image", "valkey:7.4-alpine" + DIGEST), "image-digest", "redis", None),
    *[
        (put(svc(name), "image", image + DIGEST), "image-digest", name, None)
        for name, image in (
            ("db", "evil.example/postgres:16-alpine"),
            ("redis", "ghcr.io/someone/redis:7.4-alpine"),
        )
    ],
    *[
        (
            put(env("api"), "API_TRUSTED_PROXIES", value),
            "trusted-proxy",
            "api",
            "API_TRUSTED_PROXIES",
        )
        for value in ("*", "10.20.0.0/24", "10.20.0.5, 10.20.0.6", "${OTHER:?адрес}")
    ],
    (put(svc("migrate"), "volumes", BIND_APP), "workdir-volume", "migrate", None),
    (put(svc("migrate"), "configs", APP_CONFIG), "workdir-volume", "migrate", None),
    (put(svc("migrate"), "configs", [{"source": "app"}]), "workdir-volume", "migrate", None),
    (put(svc("api"), "secrets", KEY_IN_APP), "workdir-volume", "api", None),
    (
        put(svc("api"), "volumes", ["/srv/copy/backend:/app/backend:ro"]),
        "workdir-volume",
        "api",
        None,
    ),
]
SECRETS = [
    (drop(env("api"), "JWT_SECRET"), "required-vars", "api", "JWT_SECRET"),
    (drop(env("migrate"), "DATABASE_URL"), "required-vars", "migrate", "DATABASE_URL"),
    (put(env("api"), "DATABASE_URL", DB_LITERAL), "secret-form", "api", "DATABASE_URL"),
    (put(env("api"), "JWT_SECRET", "http://a"), "secret-form", "api", "JWT_SECRET"),
    *[
        (put(env("api"), var, value), "secret-form", "api", var)
        for var, value in (
            ("REDIS_URL", "redis://redis:6379/0?password=x"),
            ("REDIS_URL", "redis://redis:6379/0#x"),
            ("DATABASE_URL", "postgresql+asyncpg://a:${DB_PASSWORD:?пароль}@db/a"),
        )
    ],
    (
        put(env("api"), "DATABASE_URL", "${API_DATABASE_URL:-}"),
        "secret-form",
        "api",
        "DATABASE_URL",
    ),
    (
        put(env("db"), "POSTGRES_PASSWORD", "literal-pass-12"),
        "secret-form",
        "db",
        "POSTGRES_PASSWORD",
    ),
    (
        put(env("migrate"), "JWT_SECRET", "${JWT_SECRET:?секрет}"),
        "foreign-secret",
        "migrate",
        "JWT_SECRET",
    ),
    (
        put(svc("redis"), "environment", {"DATABASE_URL": "${API_DATABASE_URL:?адрес}"}),
        "foreign-secret",
        "redis",
        "DATABASE_URL",
    ),
    (put(env("api"), "LOG_FROMAT", "json"), "unknown-var", "api", "LOG_FROMAT"),
    (put(svc("redis"), "env_file", ENV_FILE), "env-file", "redis", None),
    (put(svc("migrate"), "secrets", KEY_MOUNT), "master-key", "migrate", None),
]


def ids(table: list[tuple]) -> list[str]:
    return [f"{code} {service or 'стек'}" for _, code, service, _ in table]


def test_a71_a72_valid_stack_has_no_violations() -> None:
    """A71, A72: верный вымышленный стек — ноль нарушений."""
    assert found(valid()) == set()


@pytest.mark.parametrize(("spoil", "code", "service", "variable"), STRUCTURE, ids=ids(STRUCTURE))
def test_a71_each_structure_rule_has_its_probe(
    spoil: Callable, code: str, service: str | None, variable: str | None
) -> None:
    """A71: проба правила устройства — ровно её нарушение с кодом и именем сервиса."""
    config = valid()
    spoil(config)
    assert found(config) == {(code, service, variable)}


@pytest.mark.parametrize(("spoil", "code", "service", "variable"), SECRETS, ids=ids(SECRETS))
def test_a72_each_secret_rule_has_its_probe(
    spoil: Callable, code: str, service: str | None, variable: str | None
) -> None:
    """A72: проба правила секретов — ровно её нарушение: код, сервис и имя переменной."""
    config = valid()
    spoil(config)
    assert found(config) == {(code, service, variable)}


def test_a71_ports_as_raw_strings_are_read_like_objects() -> None:
    """A71: в списке портов есть `${…}` — compose отдаёт весь список строками, и голая
    `"127.0.0.1:8082:80"` рядом тоже строка; правило читает её, как объект."""
    loopback = ["127.0.0.1:8082:80", "127.0.0.1:${WEB_PORT:?порт}:81", "[::1]:8443:443"]
    as_objects = [{"host_ip": "127.0.0.1", "published": "8082", "target": 80}]
    for ports in (loopback, as_objects):
        config = valid()
        config["services"]["web"] = {**WEB, "ports": ports}
        assert found(config) == set()
    for ports in (
        ["127.0.0.1:8082:80", "${WEB_PORT}:81"],
        [as_objects[0], {"published": "8081", "target": 81}],
        ["127.0.0.1:8082:80", "${WEB_HOST:?адрес}:8083:83"],
        ["85"],
    ):
        config = valid()
        config["services"]["web"] = {**WEB, "ports": ports}
        assert found(config) == {("ports", "web", None)}


def test_a72_environment_as_list_is_read_like_mapping() -> None:
    """A72: `environment` списком `K=V` и `K` читается так же, как отображением."""
    config = valid()
    api = config["services"]["api"]
    api["environment"] = [f"{k}={v}" for k, v in api["environment"].items()] + ["LOG_FORMAT"]
    assert found(config) == set()
    api["environment"] = [item for item in api["environment"] if not item.startswith("JWT_")]
    assert found(config) == {("required-vars", "api", "JWT_SECRET")}


def test_a71_a_path_that_only_starts_like_the_workdir_is_not_in_it() -> None:
    """A71: том в `/application-data` — не в рабочем каталоге `/app` (порча M9 проверяющего)."""
    stack = valid()
    stack["services"]["api"]["volumes"] = [{"type": "volume", "target": "/application-data"}]
    assert found(stack) == set()


def test_a72_config_fields_outside_the_lists_are_not_violations() -> None:
    """A72: `APP_ENV`, `LOG_FORMAT`, `API_PORT` и пулы базы у api — поля конфига вне списков."""
    config = valid()
    extra = {"LOG_FORMAT": "json", "API_PORT": 8107, "DB_POOL_SIZE": 5, "DB_MAX_OVERFLOW": 5}
    config["services"]["api"]["environment"].update(extra)
    assert found(config) == set()


def test_a72_secret_field_inside_a_union_is_still_a_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """A72: поле `SecretStr | None` — тоже секрет: правила секретов его не теряют."""

    class Extra(config.Settings):
        token: SecretStr | None = Field(None, validation_alias="AUDIT_TOKEN")

    monkeypatch.setattr(config, "DOMAINS", (*config.DOMAINS, Extra))
    stack = valid()
    stack["services"]["api"]["environment"]["AUDIT_TOKEN"] = "${AUDIT_TOKEN:?токен}"
    assert found(stack) == {("foreign-secret", "api", "AUDIT_TOKEN")}


def test_a72_rules_read_the_process_list_from_startup_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A72: список процесса — из `startup_checks.required()`: новое обязательное имя, а compose
    не правлен — красное."""
    assert startup_checks.required("migrate") == frozenset({"DATABASE_URL"})
    assert startup_checks.required("api") == frozenset(
        {"DATABASE_URL", "REDIS_URL", "JWT_SECRET", "KEYS_MASTER_KEY_FILE", "API_TRUSTED_PROXIES"}
    )
    grown = startup_checks.required("api") | {"AUDIT_NEW_SECRET"}
    monkeypatch.setitem(startup_checks._NEEDS, "api", grown)
    assert found(valid()) == {("required-vars", "api", "AUDIT_NEW_SECRET")}


def test_a72_violation_names_code_service_variable_without_value(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A72: строка нарушения — код, сервис и имя переменной; значения в выводе нет."""
    config = valid()
    config["services"]["api"]["environment"]["JWT_SECRET"] = "literal-secret-value-0123456789abc"
    code, out = run(monkeypatch, capsys, json.dumps(config))
    assert code == 1
    assert "secret-form: сервис api, переменная JWT_SECRET" in out
    assert "literal-secret-value" not in out
    assert "нарушений 1" in out


def test_a73_rule_codes_are_exactly_the_sixteen_of_a71_a72_in_order() -> None:
    """A73: перечень кодов модуля — ровно шестнадцать кодов A71 и A72, в том же порядке."""
    assert sr.RULES == CODES


def test_a73_summary_names_services_and_rules(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A73: верный стек — код 0, «сервисов 4, правил 16», нарушений 0."""
    code, out = run(monkeypatch, capsys, json.dumps(valid()))
    assert code == 0
    assert out.strip() == "итог: сервисов 4, правил 16, нарушений 0"


def interpolated() -> str:
    """Вывод с подстановкой: `${…}` раскрыты, `env_file` влит в `environment` и пропал."""
    config = valid()
    for service in config["services"].values():
        for key, value in service.get("environment", {}).items():
            if isinstance(value, str) and "${" in value:
                service["environment"][key] = "postgresql+asyncpg://a:b@db/a"
    return json.dumps(config)


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("", "пустой ввод"),
        ("   \n", "пустой ввод"),
        ("не json", "не JSON"),
        ("{}", "ноль сервисов"),
        ('{"name": "ai-geo-audit", "services": {}}', "ноль сервисов"),
        ('{"services": {"api": {"environment": "X=${Y:?r}"}}}', "незнакомого вида"),
        (interpolated(), "--no-interpolate"),
    ],
    ids=["пусто", "пробелы", "не json", "без services", "ноль сервисов", "вид", "подстановка"],
)
def test_a73_empty_or_wrong_input_is_refused_not_clean(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], text: str, reason: str
) -> None:
    """A73: пустой ввод, ноль сервисов, незнакомый вид и вывод без `--no-interpolate` — отказ с
    кодом ≠ 0, а не «нарушений нет»."""
    code, out = run(monkeypatch, capsys, text)
    assert code == 2
    assert out.startswith("отказ: ") and reason in out
    assert "нарушений" not in out
