"""Скрипт ролей `deploy/db/roles.sql` — без Docker: одни имена с кодом (A179, первая строка) и
снятие лишнего с передачей (A380, вторая строка).

Имена ролей — из `\\set`, имена переменных паролей — из `\\getenv`, ключ замка — из
`pg_advisory_xact_lock`, предел ожидания — из `lock_timeout`. Разошлось одно — тест красный и
печатает оба значения. Файл читается от корня копии по меткам `envguard`, а не `parents[N]` (L94).
"""

from __future__ import annotations

import re

import dbtools
import pytest

from ai_geo_audit import config
from ai_geo_audit.config import startup_checks
from ai_geo_audit.ops import stack_creds
from ai_geo_audit.storage import locks

SCRIPT = dbtools.ROOT / "deploy" / "db" / "roles.sql"


def script() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def psql_vars(command: str) -> dict[str, str]:
    r"""Имена, заданные строками psql `\set имя значение` или `\getenv имя переменная`."""
    return dict(re.findall(rf"(?m)^\\{command} (\w+) (\S+)$", script()))


def test_a179_role_names_are_the_process_roles() -> None:
    r"""A179, первая строка: `\set migrate_role` и `\set app_role` — роли процессов из таблицы
    `startup_checks.DB_ROLES`: миграций, api и воркера."""
    roles = psql_vars("set")
    assert roles["migrate_role"] == startup_checks.DB_ROLES["migrate"]
    assert roles["app_role"] == startup_checks.DB_ROLES["api"] == startup_checks.DB_ROLES["worker"]


def test_a179_password_variables_are_the_rule_constants() -> None:
    r"""A179, первая строка: `\getenv` паролей — имена из констант правила `db-roles`, базы —
    `POSTGRES_DB` из того же правила."""
    names = psql_vars("getenv")
    assert {names["migrate_password"], names["app_password"]} == set(stack_creds.ROLE_PASSWORDS)
    assert names["db_name"] == "POSTGRES_DB"
    assert "POSTGRES_DB" in stack_creds.DB_OWN


def test_a179_lock_key_and_wait_are_the_code_values() -> None:
    """A179, первая строка: ключ `pg_advisory_xact_lock` — `locks.MIGRATIONS`, предел
    `lock_timeout` — умолчание `MIGRATION_LOCK_WAIT_SECONDS` (120 с)."""
    text = script()
    default = config.StorageSettings.model_fields["migration_lock_wait_seconds"].default
    assert re.findall(r"pg_advisory_xact_lock\((\d+)\)", text) == [str(locks.MIGRATIONS)]
    assert re.findall(r"lock_timeout = '(\d+)s'", text) == [str(default)]


#: Снятие членств — строка `\gexec` с `format('REVOKE %I FROM %I …')`.
MEMBERS = "format('REVOKE %I FROM %I"
#: Отзыв прав роли сервиса: вид объекта — таблицы, последовательности, схема `public`, база.
RIGHTS = re.compile(
    r'^REVOKE ALL ON (ALL TABLES|ALL SEQUENCES|SCHEMA|DATABASE)\b.* FROM :"app_role"'
)
#: Что требуется у каждой команды снятия лишнего (A380, вторая строка).
NEEDED = {
    "членства": ("GRANTED BY", "CASCADE"),
    "ALL TABLES": ("CASCADE",),
    "ALL SEQUENCES": ("CASCADE",),
    "SCHEMA": ("CASCADE",),
    "DATABASE": ("CASCADE",),
}


def revoke_commands(text: str) -> list[tuple[int, str, str]]:
    """Команды снятия лишнего: номер строки скрипта, вид, строка."""
    found: list[tuple[int, str, str]] = []
    for number, line in enumerate(text.splitlines(), 1):
        if MEMBERS in line:
            found.append((number, "членства", line))
        elif match := RIGHTS.match(line):
            found.append((number, match[1], line))
    return found


def revoke_problems(text: str) -> list[str]:
    """Нет команды или в команде нет `GRANTED BY` или `CASCADE` — по строке скрипта."""
    commands = revoke_commands(text)
    kinds = [kind for _, kind, _ in commands]
    problems = [
        f"отзыв: {kind} — команд {kinds.count(kind)}" for kind in NEEDED if kinds.count(kind) != 1
    ]
    for number, kind, line in commands:
        missing = [word for word in NEEDED[kind] if word not in line]
        if missing:
            problems.append(f"{SCRIPT.name}:{number}: {kind} — нет {' и '.join(missing)}")
    return problems


def test_a380_script_revokes_members_and_rights_with_grantor_and_cascade() -> None:
    """A380, вторая строка: у снятия членств — `GRANTED BY` и `CASCADE`, у отзыва прав роли сервиса
    на таблицы, последовательности, схему и базу — `CASCADE`; команд ровно пять."""
    assert revoke_problems(script()) == []
    assert len(revoke_commands(script())) == 5


SPOILS = [
    (number, kind, word) for number, kind, _ in revoke_commands(script()) for word in NEEDED[kind]
]


@pytest.mark.parametrize(
    ("number", "kind", "word"),
    SPOILS,
    ids=[f"line-{number}-{word.replace(' ', '-').lower()}" for number, _, word in SPOILS],
)
def test_a380_spoiled_command_is_red_with_its_script_line(
    number: int, kind: str, word: str
) -> None:
    """A380, вторая строка, порчи: у любой из пяти команд снят `CASCADE` или `GRANTED BY` — красное
    с номером строки скрипта; проверка описания, а не исполнения (§12.6)."""
    lines = script().splitlines()
    lines[number - 1] = lines[number - 1].replace(f" {word}", "", 1)
    spoiled = "\n".join(lines) + "\n"
    assert revoke_problems(spoiled) == [f"{SCRIPT.name}:{number}: {kind} — нет {word}"]


@pytest.mark.parametrize("kind", list(NEEDED))
def test_a380_missing_command_is_red(kind: str) -> None:
    """A380, вторая строка: команда снятия убрана из скрипта — красное «команд 0»."""
    gone = [number for number, found, _ in revoke_commands(script()) if found == kind]
    kept = [line for number, line in enumerate(script().splitlines(), 1) if number not in gone]
    assert revoke_problems("\n".join(kept) + "\n") == [f"отзыв: {kind} — команд 0"]
