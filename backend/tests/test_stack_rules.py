"""Правила стека над выводом `docker compose config --no-interpolate --format json` (A71–A73,
A87, A88; A179, вторая строка).

Конфиги вымышленные и в той форме, что дал compose 5.7.0 (замер среза 0 в decisions): порты —
объектами или, если в списке есть `${…}`, сырыми строками; `environment` — отображением или
списком. Каждая проба — ровно своё нарушение: без правила проба проходит, и тест красный.
"""

from __future__ import annotations

import copy
import io
import json
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import Field, SecretStr

from ai_geo_audit import config
from ai_geo_audit.api import app as api_app
from ai_geo_audit.config import startup_checks
from ai_geo_audit.ops import stack_rules as sr

DIGEST = "@sha256:" + "0" * 63 + "1"
BACKEND = "ai-geo-audit-backend:local"
HEALTH = {"interval": "5s", "timeout": "3s", "retries": 12, "start_period": "10s"}
#: Скрипт ролей — том только для чтения, как в выводе compose 5.7.1 (A177).
DB_SCRIPT = {
    "type": "bind",
    "source": "/srv/copy/deploy/db/roles.sql",
    "target": "/docker-entrypoint-initdb.d/roles.sql",
    "read_only": True,
    "bind": {"create_host_path": True},
}
#: Строка пароля на входе here-doc: `$$` — раскрытие в контейнере (A178).
PASS_LINE = "requirepass $$REDIS_PASSWORD"
#: Команда redis — `sh -c`, пароль на входе here-doc; флаги наложения — после `-` (A178, A89).
REDIS_COMMAND = ["sh", "-c", f"exec redis-server - <<EOF\n{PASS_LINE}\nEOF\n"]
REDIS_HEALTH = {
    "test": [
        "CMD-SHELL",
        'REDISCLI_AUTH="$$REDIS_PASSWORD" redis-cli ping | grep -x PONG',
    ],
    **HEALTH,
}
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
    "cap-drop",
    "no-new-privileges",
    "read-only",
    "web-address",
    "trusted-is-web",
    "segments",
    "internal",
    "stop-grace",
    "db-roles",
    "redis-auth",
)
TRUSTED = "API_TRUSTED_PROXIES"

Found = set[tuple[str, str | None, str | None]]


def hardened(name: str, *nets: str) -> dict[str, Any]:
    """Урезанные права и сети сервиса в виде вывода compose 5.7.0 (A87): сети — отображением."""
    shield = {"cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"], "read_only": True}
    return shield | {"networks": dict.fromkeys(nets)}


def valid() -> dict[str, Any]:
    """Верный стек: db, redis, migrate, api, web; секреты — только `${VAR:?причина}`; права
    урезаны, сети — data, front и edge (A86, A87)."""
    stack = plain()
    nets = {"db": ("data",), "redis": ("data",), "migrate": ("data",), "api": ("data", "front")}
    for name, service in stack["services"].items():
        service.update(hardened(name, *nets[name]))
    stack["services"]["web"] = copy.deepcopy(WEB)
    stack["networks"] = {
        "data": {"internal": True, "name": "ai-geo-audit_data"},
        "front": {
            "internal": True,
            "ipam": {"config": [{"subnet": "${FRONT_SUBNET:?подсеть front}"}]},
            "name": "ai-geo-audit_front",
        },
        "edge": {"name": "ai-geo-audit_edge"},
    }
    return stack


def plain() -> dict[str, Any]:
    """Стек Ч1.5а: db, redis, migrate, api; секреты — только `${VAR:?причина}`; сроки остановки
    api и db — ровно пороги `stop-grace` (A88)."""
    return {
        "name": "ai-geo-audit",
        "services": {
            "db": {
                "image": "postgres:16-alpine" + DIGEST,
                "restart": "unless-stopped",
                "stop_grace_period": "60s",
                "environment": {
                    "POSTGRES_USER": "audit",
                    "POSTGRES_DB": "audit",
                    "POSTGRES_PASSWORD": "${DB_PASSWORD:?пароль базы}",
                    "POSTGRES_MIGRATE_PASSWORD": "${POSTGRES_MIGRATE_PASSWORD:?пароль миграций}",
                    "POSTGRES_APP_PASSWORD": "${POSTGRES_APP_PASSWORD:?пароль сервиса}",
                },
                "healthcheck": {"test": ["CMD-SHELL", "pg_isready -h 127.0.0.1 -U a"], **HEALTH},
                "volumes": [
                    {"type": "volume", "source": "db-data", "target": "/var/lib/data"},
                    copy.deepcopy(DB_SCRIPT),
                ],
            },
            "redis": {
                "image": "redis:7.4-alpine" + DIGEST,
                "user": "redis",
                "restart": "unless-stopped",
                "environment": {"REDIS_PASSWORD": "${REDIS_PASSWORD:?пароль очереди}"},
                "command": list(REDIS_COMMAND),
                "healthcheck": copy.deepcopy(REDIS_HEALTH),
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
                "stop_grace_period": "30s",
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
                    "ALERT_TELEGRAM_BOT_TOKEN": "${ALERT_TELEGRAM_BOT_TOKEN:?токен бота}",
                    "ALERT_TELEGRAM_CHAT_ID": "${ALERT_TELEGRAM_CHAT_ID:?номер чата}",
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
    "depends_on": {"api": {"condition": "service_healthy", "required": True}},
    "healthcheck": {"test": ["CMD", "true"]},
    **hardened("web", "edge"),
}
WEB["networks"]["front"] = {"ipv4_address": "${WEB_ADDRESS:?адрес web}"}
WEB_OPEN = {**WEB, "ports": [{"published": "8080", "target": 80}]}
PORT = [{"host_ip": "127.0.0.1", "published": "8080", "target": 80}]
PG_NO_HOST = {"test": ["CMD-SHELL", "pg_isready -U a"]}
PG_SOCKET = {"test": "pg_isready -h /var/run/postgresql"}
BIND_APP = [{"type": "bind", "source": "/srv/src", "target": "/app"}]
DB_LITERAL = "postgresql+asyncpg://a:literal-pass-12@db:5432/a"  # pragma: allowlist secret
ENV_FILE = [{"path": "/srv/copy/.env", "required": True}]
KEY_MOUNT = [{"source": "master_key", "target": "/run/secrets/k"}]
APP_CONFIG = [{"source": "app_env", "target": "/app/.env"}]
KEY_IN_APP = [{"source": "master_key", "target": "/app/key"}]
#: Выключенная проверка с командой: `disable` сильнее `test` (порча M3 проверяющего).
OFF = {"disable": True, "test": ["CMD", "true"]}
BOT_PAIR = ("ALERT_TELEGRAM_BOT_TOKEN", "ALERT_TELEGRAM_CHAT_ID")

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
    (drop(svc("web"), "depends_on"), "order", "web", None),
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
        put(env("redis"), "DATABASE_URL", "${API_DATABASE_URL:?адрес}"),
        "foreign-secret",
        "redis",
        "DATABASE_URL",
    ),
    # A192: пара бота — у api по списку проверки старта, токен — секрет, у migrate пара чужая.
    *[(drop(env("api"), var), "required-vars", "api", var) for var in BOT_PAIR],
    (put(env("api"), BOT_PAIR[0], "literal-token-12"), "secret-form", "api", BOT_PAIR[0]),
    *[
        (put(env("migrate"), var, f"${{{var}:?пара бота}}"), "foreign-secret", "migrate", var)
        for var in BOT_PAIR
    ],
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
    """A72, A192: список процесса — из `startup_checks.required()`, у api в нём и пара бота: новое
    обязательное имя, а compose не правлен — красное."""
    assert startup_checks.required("migrate") == frozenset({"DATABASE_URL"})
    api = {"DATABASE_URL", "REDIS_URL", "JWT_SECRET", "KEYS_MASTER_KEY_FILE", "API_TRUSTED_PROXIES"}
    assert startup_checks.required("api") == frozenset(api | set(BOT_PAIR))
    grown = startup_checks.required("api") | {"AUDIT_NEW_SECRET"}
    monkeypatch.setitem(startup_checks._NEEDS, "api", grown)
    assert found(valid()) == {("required-vars", "api", "AUDIT_NEW_SECRET")}


def test_a72_violation_names_code_service_variable_without_value(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A72: строка нарушения — код, сервис и имя переменной; значения в выводе нет."""
    config = valid()
    config["services"]["api"]["environment"]["JWT_SECRET"] = (
        "literal-secret-value-0123456789abc"  # pragma: allowlist secret
    )
    code, out = run(monkeypatch, capsys, json.dumps(config))
    assert code == 1
    assert "secret-form: сервис api, переменная JWT_SECRET" in out
    assert "literal-secret-value" not in out
    assert "нарушений 1" in out


def test_a73_a87_a88_rule_codes_are_a71_a72_a87_and_stop_grace_in_order() -> None:
    """A73, A87, A88 и A179, вторая строка: перечень кодов модуля поимённо и по порядку —
    шестнадцать кодов A71 и A72, семь кодов A87, `stop-grace` из A88, затем `db-roles` и
    `redis-auth` «Учёток хранилищ»; всего 26."""
    assert sr.RULES == CODES


def test_a73_a87_summary_names_services_and_rules(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A73, A87: верный стек — код 0, «сервисов 5, правил 26», нарушений 0."""
    code, out = run(monkeypatch, capsys, json.dumps(valid()))
    assert code == 0
    assert out.strip() == "итог: сервисов 5, правил 26, нарушений 0"


def test_a73_web_waits_for_api_by_health_or_the_order_is_red(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A73: строка web в `ORDER` — у web без `depends_on` нарушение «ждёт api не по
    service_healthy»; у web, который ждёт api по здоровью, — нет."""
    stack = valid()
    stack["services"]["web"].pop("depends_on")
    code, out = run(monkeypatch, capsys, json.dumps(stack))
    assert code == 1
    assert "order: сервис web — ждёт api не по service_healthy" in out.splitlines()


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
        ('{"networks": {"data": "internal"}, "services": {"a": {}}}', "networks незнакомого"),
        ('{"networks": ["data"], "services": {"a": {}}}', "networks незнакомого"),
        (interpolated(), "--no-interpolate"),
        (
            '{"services": {"api": {"environment": {"A": "${B:?r}"}, "stop_grace_period": "30"}}}',
            "api: stop_grace_period незнакомого вида",
        ),
        (
            '{"services": {"db": {"environment": {"A": "${B:?r}"}, "stop_grace_period": "1d"}}}',
            "db: stop_grace_period незнакомого вида",
        ),
        (
            '{"services": {"api": {"environment": {"A": "${B:?r}"}, "stop_grace_period": ""}}}',
            "api: stop_grace_period незнакомого вида",
        ),
        *[
            (
                json.dumps(
                    {
                        "services": {
                            name: {"environment": {"A": "${B:?r}"}, "stop_grace_period": term}
                        }
                    }
                ),
                f"{name}: stop_grace_period незнакомого вида",
            )
            for name, term in (
                ("api", "${API_STOP_GRACE:-30s}"),
                ("db", "1m30"),
                ("api", "-5s"),
                ("db", "30sec"),
            )
        ],
    ],
    ids=[
        "пусто",
        "пробелы",
        "не json",
        "без services",
        "ноль сервисов",
        "вид",
        "сеть-строка",
        "сети-списком",
        "подстановка",
        "срок-без-единиц",
        "срок-в-днях",
        "срок-пустой",
        "срок-ссылкой",
        "хвост-без-единицы",
        "срок-с-минусом",
        "лишние-буквы",
    ],
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


def web_front(config: dict[str, Any]) -> dict[str, Any]:
    return config["services"]["web"]["networks"]["front"]


def network(name: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    return lambda config: config["networks"][name]


WEB_IN_DATA = {"edge": None, "front": {"ipv4_address": "${WEB_ADDRESS:?адрес}"}, "data": None}
# Проба и ровно её нарушения (A87, вторая строка): код, сервис, переменная.
HARDENING = [
    (drop(svc("redis"), "cap_drop"), {("cap-drop", "redis", None)}),
    (put(svc("api"), "cap_drop", ["NET_RAW"]), {("cap-drop", "api", None)}),
    (drop(svc("db"), "security_opt"), {("no-new-privileges", "db", None)}),
    (
        put(svc("web"), "security_opt", ["no-new-privileges:false"]),
        {("no-new-privileges", "web", None)},
    ),
    (drop(svc("migrate"), "read_only"), {("read-only", "migrate", None)}),
    (put(svc("web"), "read_only", False), {("read-only", "web", None)}),
    (put(svc("db"), "read_only", False), {("read-only", "db", None)}),
    (drop(web_front, "ipv4_address"), {("web-address", "web", None)}),
    (put(web_front, "ipv4_address", "${WEB_ADDRESS:-172.16.0.14}"), {("web-address", "web", None)}),
    (put(svc("web"), "networks", {"edge": None}), {("web-address", "web", None)}),
    (drop(services, "web"), {("web-address", "web", None)}),
    (put(env("api"), TRUSTED, "172.16.0.14"), {("trusted-is-web", "api", TRUSTED)}),
    (put(web_front, "ipv4_address", "172.16.0.14"), {("trusted-is-web", "api", TRUSTED)}),
    (
        put(web_front, "ipv4_address", "${FRONT_ADDRESS:?адрес}"),
        {("trusted-is-web", "api", TRUSTED)},
    ),
    (put(svc("web"), "networks", WEB_IN_DATA), {("segments", "web", None)}),
    (put(svc("redis"), "networks", {"data": None, "edge": None}), {("segments", "redis", None)}),
    (drop(svc("api"), "networks"), {("segments", "api", None)}),
    (put(svc("db"), "networks", {"default": None}), {("segments", "db", None)}),
    (
        drop(network("data"), "internal"),
        {("internal", name, None) for name in ("db", "redis", "migrate", "api")},
    ),
    (
        put(network("front"), "internal", False),
        {("internal", "api", None), ("internal", "web", None)},
    ),
]


@pytest.mark.parametrize(
    ("spoil", "expected"),
    HARDENING,
    ids=[" ".join(sorted({f"{c} {s}" for c, s, _ in want})) for _, want in HARDENING],
)
def test_a87_each_hardening_rule_has_its_probe(spoil: Callable, expected: Found) -> None:
    """A87, вторая строка: проба каждого условия правил защиты — ровно её нарушения с кодом
    правила и именем сервиса; без правила его проба проходит, и тест красный."""
    config = valid()
    spoil(config)
    assert found(config) == expected


def both_literal(config: dict[str, Any]) -> None:
    web_front(config)["ipv4_address"] = "172.16.0.14"
    config["services"]["api"]["environment"][TRUSTED] = " 172.16.0.14"


# Соседние случаи без нарушения (L53): лишние права к ALL, адрес числом у обоих, сети списком,
# обычная сеть у web.
CALM = [
    put(svc("redis"), "cap_drop", ["NET_RAW", "ALL"]),
    put(svc("api"), "security_opt", ["seccomp=unconfined", "no-new-privileges:true"]),
    both_literal,
    put(svc("redis"), "networks", ["data"]),
    put(svc("web"), "networks", {"front": {"ipv4_address": "${WEB_ADDRESS:?а}"}, "edge": {}}),
]


@pytest.mark.parametrize(
    "change",
    CALM,
    ids=["all-и-ещё", "две-опции", "адрес-числом", "сети-списком", "edge-у-web"],
)
def test_a87_neighbour_cases_are_not_violations(change: Callable) -> None:
    """A87, вторая строка: соседние случаи — `ALL` среди других, `no-new-privileges` среди
    опций, один и тот же адрес числом у web и api, сети списком, обычная сеть у web — не
    нарушения."""
    config = valid()
    change(config)
    assert found(config) == set()


def test_a87_root_is_read_only_everywhere_and_the_exception_list_is_empty() -> None:
    """A87, первая строка: корень только для чтения у всех пяти, у db — с `tmpfs` (слово
    владельца 2026-11-23T14:12:15Z). Список исключений правила `read-only` пуст, механизм
    остаётся: вторая строка A87 — «не в списке исключений»."""
    from ai_geo_audit.ops import stack_hardening

    assert stack_hardening.WRITABLE_ROOT == {}


# Сроки мягкой остановки (A88, третья строка): проба — ровно её нарушение у своего сервиса.
STOP_GRACE = [
    (put(svc("api"), "stop_grace_period", "29s"), "api"),
    (put(svc("api"), "stop_grace_period", "15s"), "api"),
    (drop(svc("api"), "stop_grace_period"), "api"),
    (put(svc("db"), "stop_grace_period", "59s"), "db"),
    (put(svc("db"), "stop_grace_period", "30s"), "db"),
    (put(svc("db"), "stop_grace_period", "59999ms"), "db"),
    (drop(svc("db"), "stop_grace_period"), "db"),
]


@pytest.mark.parametrize(
    ("spoil", "service"),
    STOP_GRACE,
    ids=["api-29", "api-15", "api-нет", "db-59", "db-30", "db-59999ms", "db-нет"],
)
def test_a88_short_stop_grace_is_red_for_its_service(spoil: Callable, service: str) -> None:
    """A88, третья строка: у api срок меньше предела uvicorn с запасом на пулы (30 с), у db —
    меньше 60 с, без `stop_grace_period` — умолчание докера 10 с: красное `stop-grace` с именем
    сервиса."""
    config = valid()
    spoil(config)
    assert found(config) == {("stop-grace", service, None)}


@pytest.mark.parametrize(
    ("api", "db"),
    [("30s", "60s"), ("1m", "1m"), ("45s", "1m30s"), ("30000ms", "0.02h"), ("1h", "2m")],
    ids=["ровно", "минуты", "смесь", "мс-и-часы", "с-запасом"],
)
def test_a88_stop_grace_at_or_above_the_limit_is_not_a_violation(api: str, db: str) -> None:
    """A88, третья строка, соседние случаи (L53): срок ровно на пороге и выше — не нарушение;
    длительность — строкой, как в файле: секунды, минуты, часы, миллисекунды и их смесь."""
    config = valid()
    config["services"]["api"]["stop_grace_period"] = api
    config["services"]["db"]["stop_grace_period"] = db
    assert found(config) == set()


def test_a88_api_limit_is_read_from_server_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """A88, третья строка: предел api правило берёт из `server_settings()` Ч1.3
    (`timeout_graceful_shutdown`), а не из своей копии числа: предел uvicorn 25 с — и 30 с уже
    мало, нужно 35 с."""
    assert api_app.server_settings()["timeout_graceful_shutdown"] == 20
    monkeypatch.setattr(api_app, "GRACEFUL_SHUTDOWN_SECONDS", 25)
    config = valid()
    assert found(config) == {("stop-grace", "api", None)}
    config["services"]["api"]["stop_grace_period"] = "35s"
    assert found(config) == set()


def test_a88_db_term_is_judged_without_api() -> None:
    """A88, третья строка: api в стеке нет — срок db судится всё равно."""
    config = valid()
    del config["services"]["api"]
    assert ("stop-grace", "db", None) not in found(config)
    config["services"]["db"]["stop_grace_period"] = "30s"
    assert ("stop-grace", "db", None) in found(config)


def test_a88_stop_grace_line_names_the_term_and_the_need(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A88, третья строка: строка нарушения называет срок и сколько нужно; без ключа — что срок
    взят у докера."""
    config = valid()
    config["services"]["api"]["stop_grace_period"] = "29s"
    config["services"]["db"].pop("stop_grace_period")
    code, out = run(monkeypatch, capsys, json.dumps(config))
    assert code == 1
    assert out.splitlines() == [
        "stop-grace: сервис api — stop_grace_period 29 с, нужно не меньше 30 с",
        "stop-grace: сервис db — stop_grace_period нет — у докера 10 с, нужно не меньше 60 с",
        "итог: сервисов 5, правил 26, нарушений 2",
    ]


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("30s", 30.0), ("1m30s", 90.0), ("0.01h", 36.0), ("1500ms", 1.5), ("2m", 120.0)],
)
def test_a88_duration_is_read_in_seconds(text: str, seconds: float) -> None:
    """A88: длительность compose — строка, как в файле (замер compose 5.7.0, decisions Ч1.5в):
    секунды, минуты, часы и миллисекунды, по отдельности и вместе."""
    assert sr._seconds("api", text) == pytest.approx(seconds)
