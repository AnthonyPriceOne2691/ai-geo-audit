"""Правила устройства стека и его секретов над выводом
`docker compose config --no-interpolate --format json`.

Правило называется кодом, номер в перечне — только порядок вывода. Вход — только вывод с
`--no-interpolate`: с подстановкой compose раскрывает `${…}` и вливает `env_file` в
`environment`, и правило `env-file` слепнет. Признак такого входа — ни одной ссылки `${…}` в
`environment` сервисов: тогда отказ, как на пустом вводе, нуле сервисов и поле незнакомого
вида. Форма вывода зависит от содержимого файла — порты объектами или сырыми строками,
переменные отображением или списком; правила читают обе (замер compose 5.7.0, decisions Ч1.5а).

Запуск — внутри собранного образа: `python -m ai_geo_audit.ops.stack_rules < config.json`;
код 0 — нарушений нет, 1 — нарушения, 2 — отказ. Предел мягкой остановки api правило
`stop-grace` берёт из настроек uvicorn — `server_settings()` (A88), а не из своей копии числа.
"""

from __future__ import annotations

import ipaddress
import json
import re
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, get_args
from urllib.parse import urlsplit

from pydantic import SecretStr

from ai_geo_audit import config
from ai_geo_audit.api.app import server_settings
from ai_geo_audit.config import startup_checks
from ai_geo_audit.ops import stack_hardening as hard

STACK_NAME = "ai-geo-audit"
#: Образы из реестра: имя и тег линии (слово владельца 2026-11-19T10:40Z, дайджесты — срез 0).
IMAGE_LINES = {"postgres": "16-alpine", "redis": "7.4-alpine"}
#: Публикует порт только web и только на петле; сам web — Ч1.5б.
PUBLISHERS = frozenset({"web"})
#: Одноразовые сервисы: без перезапуска и без проверки здоровья.
ONE_SHOT = frozenset({"migrate"})
#: Рабочий каталог образа сервиса: там конфиг ищет `.env` (`deploy/backend/Dockerfile`).
IMAGE_WORKDIR = "/app"
#: Секрет compose с мастер-ключом: только у процессов, которым нужен `KEYS_MASTER_KEY_FILE`.
MASTER_KEY_NAME = "master_key"
#: Секреты образов, а не конфига: пароль суперпользователя базы задаёт образ postgres.
IMAGE_SECRETS = frozenset({"POSTGRES_PASSWORD"})
#: Запас к пределу мягкой остановки uvicorn у api: после неё процесс ещё закрывает пул базы, а
#: своего предела закрытия пулов в коде нет — 10 с — оценка (decisions Ч1.5в, срез 0).
POOL_CLOSE_SECONDS = 10
#: Срок остановки базы: контрольная точка Postgres при остановке.
DB_STOP_SECONDS = 60
#: Срок остановки без `stop_grace_period` — умолчание докера.
DOCKER_STOP_SECONDS = 10
#: Кто кого ждёт и по какому условию.
ORDER = (
    ("api", "migrate", "service_completed_successfully"),
    ("migrate", "db", "service_healthy"),
    ("web", "api", "service_healthy"),
)
#: Виды полей, которые читают правила; другой вид — отказ, а не «нарушений нет».
SHAPES: dict[str, tuple[type, ...]] = {
    "environment": (dict, list),
    "ports": (list,),
    "volumes": (list,),
    "secrets": (list,),
    "configs": (list,),
    "env_file": (list,),
    "depends_on": (dict, list),
    "healthcheck": (dict,),
    "networks": (dict, list),
    "cap_drop": (list,),
    "security_opt": (list,),
}

#: Сервис и процесс сопоставляются по имени: api — `api`, migrate — `migrate`.
_PROCESSES: dict[str, startup_checks.Process] = {p: p for p in get_args(startup_checks.Process)}
_REF = re.compile(r"\$\{[^}]*\}")
_STRICT_REF = hard.STRICT_REF
_IMAGE = re.compile(r"(?P<name>[a-z0-9._/-]+):(?P<tag>[\w.-]+)@sha256:[0-9a-f]{64}")
_PG_HOST = re.compile(r"(?:^|\s)(?:-h|--host)(?:=|\s+)(\S+)")
_DURATION = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")
_UNIT_SECONDS = {"h": 3600.0, "m": 60.0, "s": 1.0, "ms": 0.001}

Finding = tuple[str | None, str | None, str]


class InputRefusedError(ValueError):
    """Ввод не годится для правил: проверять нечего или нечем."""


@dataclass(frozen=True, slots=True)
class Violation:
    """Код правила, сервис (нет — весь стек) и имя переменной; значения нет нигде."""

    code: str
    service: str | None
    variable: str | None
    why: str

    def line(self) -> str:
        where = f"сервис {self.service}" if self.service else "стек"
        if self.variable:
            where += f", переменная {self.variable}"
        return f"{self.code}: {where} — {self.why}"


@dataclass(frozen=True, slots=True)
class Stack:
    name: object
    services: dict[str, dict[str, Any]]
    env: dict[str, dict[str, object]]
    networks: dict[str, dict[str, Any]]


def _own(service: str) -> frozenset[str]:
    """Список проверки старта процесса сервиса (по имени); у db и redis процесса нет."""
    process = _PROCESSES.get(service)
    return startup_checks.required(process) if process else frozenset()


def _environment(service: str, raw: object) -> dict[str, object]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        pairs = (item.partition("=") for item in raw)
        return {name: value if sep else None for name, sep, value in pairs}
    if raw is None:
        return {}
    raise InputRefusedError(f"сервис {service}: environment незнакомого вида")


def parse(raw: object) -> Stack:
    """Стек из вывода compose; ввод, на котором правила молча ослепли бы, — отказ."""
    services = raw.get("services") if isinstance(raw, dict) else None
    if not isinstance(raw, dict) or not isinstance(services, dict) or not services:
        raise InputRefusedError("ноль сервисов — правилам нечего проверять")
    networks = raw.get("networks") or {}
    if not isinstance(networks, dict) or not all(isinstance(n, dict) for n in networks.values()):
        raise InputRefusedError("networks незнакомого вида")
    env: dict[str, dict[str, object]] = {}
    for name, service in services.items():
        if not isinstance(service, dict):
            raise InputRefusedError(f"сервис {name} незнакомого вида")
        for key, kinds in SHAPES.items():
            if service.get(key) is not None and not isinstance(service[key], kinds):
                raise InputRefusedError(f"сервис {name}: {key} незнакомого вида")
        env[name] = _environment(name, service.get("environment"))
    values = (value for found in env.values() for value in found.values())
    if not any(isinstance(value, str) and "${" in value for value in values):
        raise InputRefusedError(
            "в environment нет ни одной ссылки ${…} — похоже на вывод без --no-interpolate: "
            "с подстановкой правила не видят env_file"
        )
    return Stack(raw.get("name"), services, env, networks)


def _stack_name(stack: Stack) -> Iterator[Finding]:
    if stack.name != STACK_NAME:
        yield None, None, f"имя стека — не {STACK_NAME}"


def _port_host(entry: object) -> object:
    """Адрес хоста порта: у объекта — поле, у короткой записи — первая из трёх частей.
    Ссылка `${…}` заменяется знаком `$`: адресом она не читается."""
    if not isinstance(entry, str):
        return entry.get("host_ip") if isinstance(entry, dict) else None
    text = _REF.sub("$", entry)
    if text.startswith("["):
        return text[1 : text.find("]")]
    parts = text.split(":")
    return parts[0] if len(parts) == 3 else None


def _loopback(address: object) -> bool:
    try:
        return ipaddress.ip_address(str(address)).is_loopback
    except ValueError:
        return False


def _ports(stack: Stack) -> Iterator[Finding]:
    for name, service in stack.services.items():
        entries = service.get("ports") or []
        if service.get("network_mode") == "host":
            yield name, None, "сеть хоста: сервис слушает интерфейсы машины без ports"
        elif entries and name not in PUBLISHERS:
            yield name, None, "порт наружу: публикует только web"
        elif not all(_loopback(_port_host(entry)) for entry in entries):
            yield name, None, "порт web не на петле"


def _health_test(service: dict[str, Any]) -> str:
    """Команда проверки здоровья строкой; выключенная, пустая или `NONE` — пустая строка."""
    check = service.get("healthcheck") or {}
    test = check.get("test")
    if check.get("disable") or not test:
        return ""
    words = [test] if isinstance(test, str) else [str(word) for word in test]
    if words[0] in ("CMD", "CMD-SHELL"):
        words = words[1:]
    text = " ".join(words).strip()
    return "" if text == "NONE" else text


def _healthcheck(stack: Stack) -> Iterator[Finding]:
    for name, service in stack.services.items():
        if name not in ONE_SHOT and not _health_test(service):
            yield name, None, "долгоживущий сервис без проверки здоровья"


def _restart(stack: Stack) -> Iterator[Finding]:
    for name, service in stack.services.items():
        policy = service.get("restart")
        if name in ONE_SHOT and policy not in (None, "no"):
            yield name, None, "у одноразового сервиса перезапуск не no"
        elif name not in ONE_SHOT and policy != "unless-stopped":
            yield name, None, "у долгоживущего сервиса перезапуск не unless-stopped"


def _depends(service: dict[str, Any]) -> dict[str, object]:
    deps = service.get("depends_on") or {}
    if isinstance(deps, list):
        return dict.fromkeys(deps, "service_started")
    return {k: v.get("condition") if isinstance(v, dict) else None for k, v in deps.items()}


def _order(stack: Stack) -> Iterator[Finding]:
    for name, target, condition in ORDER:
        if name in stack.services and _depends(stack.services[name]).get(target) != condition:
            yield name, None, f"ждёт {target} не по {condition}"


def _init(stack: Stack) -> Iterator[Finding]:
    for name, service in stack.services.items():
        if name in _PROCESSES and service.get("init") is not True:
            yield name, None, "нет init: true"


def _db_health_tcp(stack: Stack) -> Iterator[Finding]:
    test = _health_test(stack.services.get("db") or {})
    host = _PG_HOST.search(test)
    if test and ("pg_isready" not in test or not host or host[1].startswith("/")):
        yield "db", None, "проверка базы — не pg_isready по TCP с адресом"


def _image_digest(stack: Stack) -> Iterator[Finding]:
    built = {service.get("image") for service in stack.services.values() if service.get("build")}
    for name, service in stack.services.items():
        image = service.get("image")
        if not image or image in built:
            continue
        match = _IMAGE.fullmatch(str(image))
        repo = match["name"].removeprefix("docker.io/").removeprefix("library/") if match else ""
        if not match or IMAGE_LINES.get(repo) != match["tag"]:
            yield name, None, "образ из реестра — не «имя:тег линии@sha256:…» из списка"


def _trusted_proxy(stack: Stack) -> Iterator[Finding]:
    var = "API_TRUSTED_PROXIES"
    for name, values in stack.env.items():
        value = values.get(var)
        if var not in _own(name) or value in (None, ""):
            continue  # нет переменной — правило required-vars
        if hard.trusted(value) is None:
            yield name, var, f"доверенный прокси — не один адрес из {hard.WEB_ADDRESS}"


def _volume_target(entry: object) -> str:
    if isinstance(entry, dict):
        return str(entry.get("target") or "")
    parts = _REF.sub("$", str(entry)).split(":")
    return parts[1] if len(parts) > 1 else parts[0]


def _mount_targets(service: dict[str, Any]) -> list[str]:
    """Куда в контейнере ложатся тома, configs и secrets; у двух последних путь бывает
    относительным или пустым — тогда по умолчанию compose."""
    targets = [_volume_target(entry) for entry in service.get("volumes") or []]
    for key, base in (("configs", "/"), ("secrets", "/run/secrets/")):
        for entry in service.get(key) or []:
            item = entry if isinstance(entry, dict) else {"source": entry}
            target = str(item.get("target") or item.get("source") or "")
            targets.append(target if target.startswith("/") else base + target)
    return targets


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def _workdir_volume(stack: Stack) -> Iterator[Finding]:
    for name, service in stack.services.items():
        roots = {IMAGE_WORKDIR, str(service.get("working_dir") or IMAGE_WORKDIR)}
        targets = _mount_targets(service)
        if name in _PROCESSES and any(_under(t, root) for t in targets for root in roots):
            yield name, None, "том, config или secret в рабочем каталоге образа"


def _required_vars(stack: Stack) -> Iterator[Finding]:
    for name, values in stack.env.items():
        for var in sorted(_own(name)):
            if values.get(var) in (None, ""):
                yield name, var, "нет переменной из списка проверки старта"


def _holds_secret(annotation: object) -> bool:
    """`SecretStr` сам или внутри объединения (`SecretStr | None`, `Optional[SecretStr]`)."""
    return annotation is SecretStr or any(map(_holds_secret, get_args(annotation)))


def _secret_names() -> frozenset[str]:
    """Секреты конфига — поля с `SecretStr`; имя переменной стоит у поля."""
    return frozenset(
        str(field.validation_alias)
        for domain in config.DOMAINS
        for field in domain.model_fields.values()
        if _holds_secret(field.annotation)
    )


def _plain_url(value: object) -> bool:
    """Адрес без пароля, запроса и ссылок `${…}` секрета не несёт: так до «Учёток хранилищ»
    задан адрес очереди у api. Освобождает только переменные адресов — с именем на `_URL`."""
    text = str(value)
    try:
        parts = urlsplit(text)
        password = parts.password
    except ValueError:
        return False
    plain = password is None and not (parts.query or parts.fragment) and "${" not in text
    return plain and bool(parts.scheme and parts.netloc)


def _secret_form(stack: Stack) -> Iterator[Finding]:
    secrets = _secret_names() | IMAGE_SECRETS
    for name, values in stack.env.items():
        for var in sorted(secrets & values.keys()):
            value = values[var]
            plain = var.endswith("_URL") and _plain_url(value)
            if value in (None, "") or _STRICT_REF.fullmatch(str(value)) or plain:
                continue  # пустое у своего процесса — правило required-vars
            yield name, var, "секрет — не ${VAR:?причина}"


def _foreign_secret(stack: Stack) -> Iterator[Finding]:
    listed = _secret_names().union(*map(startup_checks.required, _PROCESSES.values()))
    for name, values in stack.env.items():
        for var in sorted((listed & values.keys()) - _own(name)):
            yield name, var, "переменная из чужого списка проверки старта"


def _unknown_var(stack: Stack) -> Iterator[Finding]:
    known = frozenset(config.env_names())
    for name, values in stack.env.items():
        if name in _PROCESSES:
            for var in sorted(values.keys() - known):
                yield name, var, "такого поля в конфиге нет"


def _env_file(stack: Stack) -> Iterator[Finding]:
    for name, service in stack.services.items():
        if service.get("env_file"):
            yield name, None, "env_file: весь файл уходит в контейнер"


def _master_key(stack: Stack) -> Iterator[Finding]:
    for name, service in stack.services.items():
        mounted = service.get("secrets") or []
        sources = {e.get("source") if isinstance(e, dict) else e for e in mounted}
        if MASTER_KEY_NAME in sources and "KEYS_MASTER_KEY_FILE" not in _own(name):
            yield name, None, "смонтирован файл мастер-ключа"


def _seconds(service: str, raw: object) -> float:
    """Длительность compose в секундах: строка, как в файле, — `30s`, `1m30s`, `500ms` (замер
    compose 5.7.0, decisions Ч1.5в); другой вид — отказ."""
    text = str(raw)
    parts = _DURATION.findall(text)
    if not parts or "".join(map("".join, parts)) != text:
        raise InputRefusedError(f"сервис {service}: stop_grace_period незнакомого вида")
    return sum(float(number) * _UNIT_SECONDS[unit] for number, unit in parts)


def _stop_needs() -> tuple[tuple[str, float], ...]:
    """Сроки остановки: у api — мягкая остановка uvicorn из `server_settings()` и запас на
    закрытие пулов, у db — контрольная точка."""
    graceful = float(str(server_settings()["timeout_graceful_shutdown"]))
    return ("api", graceful + POOL_CLOSE_SECONDS), ("db", DB_STOP_SECONDS)


def _stop_grace(stack: Stack) -> Iterator[Finding]:
    for name, need in _stop_needs():
        if name not in stack.services:
            continue
        raw = stack.services[name].get("stop_grace_period")
        got = DOCKER_STOP_SECONDS if raw is None else _seconds(name, raw)
        if got < need:
            said = "stop_grace_period нет — у докера" if raw is None else "stop_grace_period"
            yield name, None, f"{said} {got:g} с, нужно не меньше {need:g} с"


CHECKS: tuple[tuple[str, Callable[[Stack], Iterator[Finding]]], ...] = (
    ("stack-name", _stack_name),
    ("ports", _ports),
    ("healthcheck", _healthcheck),
    ("restart", _restart),
    ("order", _order),
    ("init", _init),
    ("db-health-tcp", _db_health_tcp),
    ("image-digest", _image_digest),
    ("trusted-proxy", _trusted_proxy),
    ("workdir-volume", _workdir_volume),
    ("required-vars", _required_vars),
    ("secret-form", _secret_form),
    ("foreign-secret", _foreign_secret),
    ("unknown-var", _unknown_var),
    ("env-file", _env_file),
    ("master-key", _master_key),
    ("cap-drop", hard.cap_drop),
    ("no-new-privileges", hard.no_new_privileges),
    ("read-only", hard.read_only),
    ("web-address", hard.web_address),
    ("trusted-is-web", hard.trusted_is_web),
    ("segments", hard.segments),
    ("internal", hard.internal),
    ("stop-grace", _stop_grace),
)
RULES = tuple(code for code, _ in CHECKS)


def check(raw: object) -> list[Violation]:
    """Нарушения стека в порядке перечня; негодный ввод — `InputRefusedError`."""
    stack = parse(raw)
    return [Violation(code, *finding) for code, rule in CHECKS for finding in rule(stack)]


def run(text: str) -> tuple[int, list[str]]:
    """Код выхода и строки вывода: нарушения и итог либо одна строка отказа."""
    if not text.strip():
        return 2, ["отказ: пустой ввод"]
    try:
        raw = json.loads(text)
        found = check(raw)
    except InputRefusedError as refusal:
        return 2, [f"отказ: {refusal}"]
    except json.JSONDecodeError:
        return 2, ["отказ: ввод не JSON"]
    total = f"итог: сервисов {len(raw['services'])}, правил {len(RULES)}, нарушений {len(found)}"
    return (1 if found else 0), [violation.line() for violation in found] + [total]


def main() -> int:
    code, lines = run(sys.stdin.read())
    sys.stdout.write("\n".join(lines) + "\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
