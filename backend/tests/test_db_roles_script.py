"""Скрипт ролей `deploy/db/roles.sql` и код — одни имена (A179, первая строка): без Docker.

Имена ролей — из `\\set`, имена переменных паролей — из `\\getenv`, ключ замка — из
`pg_advisory_xact_lock`, предел ожидания — из `lock_timeout`. Разошлось одно — тест красный и
печатает оба значения. Файл читается от корня копии по меткам `envguard`, а не `parents[N]` (L94).
"""

from __future__ import annotations

import re

import dbtools

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
