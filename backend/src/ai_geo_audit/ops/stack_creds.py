"""Правила учёток хранилищ (A177, A178): роли базы — `db-roles` — и пароль очереди — `redis-auth`.
Перечень, порядок и вывод — в `stack_rules`: там коды 25 и 26 ведут сюда.

Вход — `Stack` правил стека (вывод `docker compose config --no-interpolate`). Раскрытием пароля в
контейнере правило считает только `$$`: один `$` раскрывает compose, и значение ложится в описание
контейнера и в журнал выката (проба 3 среза 0 «Учёток-2» на compose 5.7.1). Строку оболочки правило
разбирает само — `sh -c` с here-doc, — а не регуляркой по файлу (L52, L69). Модуль берёт `Stack`
только для типов, поэтому `stack_rules` импортирует отсюда без круга.
"""

from __future__ import annotations

import itertools
import re
import shlex
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from ai_geo_audit.ops.stack_hardening import STRICT_REF

if TYPE_CHECKING:
    from ai_geo_audit.ops.stack_rules import Finding, Stack

#: Пароли ролей базы — переменные контейнера db; их же читает скрипт ролей (A179).
ROLE_PASSWORDS = ("POSTGRES_MIGRATE_PASSWORD", "POSTGRES_APP_PASSWORD")
#: Что db задаёт явно: на это опираются скрипт ролей и проверка здоровья.
DB_OWN = ("POSTGRES_USER", "POSTGRES_DB")
#: Пароли базы — суперпользователя и ролей: только у db.
DB_PASSWORDS = frozenset({"POSTGRES_PASSWORD", *ROLE_PASSWORDS})
#: Каталог образа postgres: на пустом томе он исполняет скрипты оттуда.
INIT_DIR = "/docker-entrypoint-initdb.d"
#: Переменная сервера, из которой процесс берёт адрес базы.
ADDRESSES = {"migrate": "MIGRATE_DATABASE_URL", "api": "API_DATABASE_URL"}
#: Пароль очереди — переменная контейнера redis; в контейнере он раскрывается из `$$`.
QUEUE_VAR = "REDIS_PASSWORD"
IN_CONTAINER = "$$REDIS_PASSWORD"
#: Короткая запись тома: двоеточие внутри ссылки `${…}` — не разделитель.
_REF = re.compile(r"\$\{[^}]*\}")
#: Начало here-doc словом без кавычек: в слове с кавычками оболочка не раскрывает `$…`.
_HERE_DOC = re.compile(r"<<-?\s*\w+\s*$")
#: Пароль очереди у проверки здоровья — раскрытие в контейнере, без одинарных кавычек.
_HEALTH_AUTH = re.compile(r'REDISCLI_AUTH="?\$\$REDIS_PASSWORD"?(?=\s|$)')


def _strict(value: object) -> str | None:
    """Имя переменной ссылки `${VAR:?причина}`; другой вид — None."""
    match = STRICT_REF.fullmatch(str(value))
    return match[1] if match else None


def _mounts(service: dict[str, Any]) -> Iterator[tuple[str, bool]]:
    """Точки монтирования томов и «только чтение»; короткая запись — `источник:путь:режимы`."""
    for entry in service.get("volumes") or []:
        if isinstance(entry, dict):
            yield str(entry.get("target") or ""), entry.get("read_only") is True
            continue
        parts = _REF.sub("$", str(entry)).split(":")
        modes = parts[2].split(",") if len(parts) > 2 else []
        yield parts[1] if len(parts) > 1 else parts[0], "ro" in modes


def _in_init(target: str) -> bool:
    return target == INIT_DIR or target.startswith(INIT_DIR + "/")


def _db(stack: Stack) -> Iterator[Finding]:
    """Условия у самого db: явные имена, пароли ролей ссылкой, скрипт ролей — только для чтения."""
    values = stack.env["db"]
    for var in DB_OWN:
        if not values.get(var):
            yield "db", var, "не задана явно"
    for var in ROLE_PASSWORDS:
        if _strict(values.get(var)) is None:
            yield "db", var, "пароль роли — не ${VAR:?причина}"
    modes = [read_only for target, read_only in _mounts(stack.services["db"]) if _in_init(target)]
    if not modes:
        yield "db", None, f"скрипт ролей не смонтирован в {INIT_DIR}"
    elif not all(modes):
        yield "db", None, f"скрипт ролей смонтирован в {INIT_DIR} на запись"


def db_roles(stack: Stack) -> Iterator[Finding]:
    """A177: роли базы ссылками сервера и только у db, скрипт ролей на месте, адреса процессов — из
    своих переменных."""
    for name, values in stack.env.items():
        if name != "db":
            for var in sorted(DB_PASSWORDS & values.keys()):
                yield name, var, "пароль базы — только у db"
    for name, var in ADDRESSES.items():
        ref = _strict(stack.env.get(name, {}).get("DATABASE_URL"))
        if ref is not None and ref != var:  # нет адреса или литерал — required-vars и secret-form
            yield name, "DATABASE_URL", f"адрес базы — не из {var}"
    if "db" in stack.services:
        yield from _db(stack)


def _shell(text: str) -> list[str]:
    """Слова строки по разбору оболочки; незакрытая кавычка — пусто: вид не тот."""
    try:
        return shlex.split(text)
    except ValueError:
        return []


def _script(service: dict[str, Any]) -> str | None:
    """Скрипт `sh -c` из команды списком или строкой; другой вид — None."""
    raw = service.get("command") or []
    words = _shell(raw) if isinstance(raw, str) else [str(word) for word in raw]
    return words[2] if len(words) == 3 and words[:2] == ["sh", "-c"] else None


def _auth_command(service: dict[str, Any]) -> bool:
    """Команда redis: `exec redis-server -`, без пароля аргументом, а на входе here-doc — ровно одна
    строка `requirepass $$REDIS_PASSWORD`."""
    script = _script(service)
    if script is None:
        return False
    first, _, rest = script.partition("\n")
    here_doc = _HERE_DOC.search(first)
    if here_doc is None:
        return False
    words = _shell(first[: here_doc.start()])
    lines = [line.split() for line in rest.splitlines()]
    passes = [line for line in lines if line[:1] == ["requirepass"]]
    by_argument = any(word.startswith("--requirepass") for word in words)
    started = words[:3] == ["exec", "redis-server", "-"] and not by_argument
    return started and passes == [["requirepass", IN_CONTAINER]]


def _health(service: dict[str, Any]) -> str | None:
    """Проверка здоровья одной строкой; нет её, выключена или `NONE` — None: судит `healthcheck`."""
    check = service.get("healthcheck") or {}
    test = check.get("test")
    if check.get("disable") or not test:
        return None
    words = [str(word) for word in test] if isinstance(test, list) else [str(test)]
    if words[0] in ("CMD", "CMD-SHELL"):
        words = words[1:]
    return None if words == ["NONE"] else " ".join(words)


def _auth_health(service: dict[str, Any]) -> bool:
    """Проверка здоровья redis: `REDISCLI_AUTH` раскрытием `$$REDIS_PASSWORD` перед `redis-cli` и
    сверка ответа с `PONG` после `|`."""
    text = _health(service)
    if text is None:
        return True
    auth = _HEALTH_AUTH.search(text)
    if auth is None or auth.start() > text.find("redis-cli"):
        return False
    words = _shell(text)
    answered = words[words.index("|") + 1 :] if "|" in words else []
    return "grep" in answered and "PONG" in answered


def _has_password(url: str) -> bool:
    try:
        return urlsplit(url).password is not None
    except ValueError:
        return False


def _password_flag(service: dict[str, Any]) -> bool:
    """Пароль на виду у процессов — в команде или проверке здоровья: `-a` или `--pass` у redis-cli,
    `-u` с паролем в адресе."""
    command = service.get("command") or []
    parts = [command] if isinstance(command, str) else [str(part) for part in command]
    words = [word for part in parts for word in _shell(part)] + _shell(_health(service) or "")
    flagged = any(word in ("-a", "--pass") or word.startswith("--pass=") for word in words)
    by_url = any(flag == "-u" and _has_password(url) for flag, url in itertools.pairwise(words))
    return ("redis-cli" in words and flagged) or by_url


def _redis(value: object, service: dict[str, Any]) -> Iterator[Finding]:
    """Условия у самого redis: пароль ссылкой, пользователь по имени, команда, проверка здоровья."""
    if _strict(value) is None:
        yield "redis", QUEUE_VAR, "пароль очереди — не ${VAR:?причина}"
    if service.get("user") != "redis":
        yield "redis", None, "нет user: redis"
    if not _auth_command(service):
        yield "redis", None, "команда — не sh -c с exec redis-server - и requirepass на входе"
    if not _auth_health(service):
        yield "redis", None, "проверка здоровья — без REDISCLI_AUTH или сверки с PONG"


def redis_auth(stack: Stack) -> Iterator[Finding]:
    """A178: пароль очереди — ссылкой сервера и только у redis, команда и проверка здоровья берут
    его раскрытием в контейнере, флагов с паролем нет ни у кого."""
    for name, values in stack.env.items():
        if name != "redis" and QUEUE_VAR in values:
            yield name, QUEUE_VAR, "пароль очереди — только у redis"
    for name, service in stack.services.items():
        if _password_flag(service):
            yield name, None, "пароль флагом redis-cli виден в списке процессов"
    redis = stack.services.get("redis")
    if redis is not None:
        yield from _redis(stack.env["redis"].get(QUEUE_VAR), redis)
