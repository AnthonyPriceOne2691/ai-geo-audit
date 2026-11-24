"""Шаги оракула «Учёток-1» — роли базы на живом стеке: права роли сервиса и роли миграций
изнутри контейнеров процессов (A85). Проба базы — `scripts/oracle_db_probe.py` на входе
`python -` (L87); запросы суперпользователя — psql в контейнере db через сокет.

Импорт в одну сторону: `stack_oracle.py` ставит эти шаги на живом стеке после шагов остановки
api и до A67.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from stack_oracle_compose import StepError, alembic, compose

if TYPE_CHECKING:
    from stack_oracle import Run

PROBE = "scripts/oracle_db_probe.py"
APP, MIGRATE = "audit_app", "audit_migrate"
DENIED = "42501"
#: Где идёт проба базы: в контейнере api — `exec`, у migrate — разовый контейнер его адресом.
WHERE = {"api": ("exec", "-T", "api"), "migrate": ("run", "--rm", "-T", "--no-deps", "migrate")}
#: Суперпользователь образа в контейнере db через сокет: `local` — `trust` (срез 0, проба 1).
SUPERUSER: tuple[str, ...] = ("exec", "-T", "db", "psql", "-X", "-q", "-At")
SUPERUSER += ("-v", "ON_ERROR_STOP=1", "-U", "audit", "-d", "audit")
#: A85, вторая строка: владельцы базы и `alembic_version`, вход и особые права обеих ролей.
OWNERS = """
SELECT 'база ' || pg_get_userbyid(datdba) FROM pg_database WHERE datname = 'audit';
SELECT 'alembic_version ' || tableowner FROM pg_tables
 WHERE schemaname = 'public' AND tablename = 'alembic_version';
SELECT 'роль ' || rolname || ' login=' || rolcanlogin || ' super=' || rolsuper
       || ' createrole=' || rolcreaterole || ' createdb=' || rolcreatedb
       || ' repl=' || rolreplication || ' bypassrls=' || rolbypassrls
  FROM pg_roles WHERE rolname IN ('audit_app', 'audit_migrate') ORDER BY rolname;
"""
_RIGHTS = "createrole=false createdb=false repl=false bypassrls=false"
OWNER_ROWS = [
    f"база {MIGRATE}",
    f"alembic_version {MIGRATE}",
    f"роль {APP} login=true super=false {_RIGHTS}",
    f"роль {MIGRATE} login=true super=false {_RIGHTS}",
]
#: A85, третья строка: у роли сервиса на таблице пробы нет `REFERENCES` и `TRIGGER`.
PRIVILEGES = """
SELECT 'REFERENCES ' || has_table_privilege('audit_app', 'oracle_probe', 'REFERENCES');
SELECT 'TRIGGER ' || has_table_privilege('audit_app', 'oracle_probe', 'TRIGGER');
"""
PRIVILEGE_ROWS = ["REFERENCES false", "TRIGGER false"]
LEFT = """
SELECT 'oracle_probe ' || count(*) FROM pg_tables
 WHERE schemaname = 'public' AND tablename = 'oracle_probe';
"""


def probe(run: Run, where: str, name: str) -> dict[str, Any]:
    """Набор пробы базы в контейнере процесса; ответ — последняя строка вывода, JSON."""
    script = (run.copy / PROBE).read_bytes()
    out = compose(run, *WHERE[where], "python", "-", name, stdin=script)
    found: dict[str, Any] = json.loads(out.strip().splitlines()[-1])
    return found


def superuser(run: Run, sql: str) -> list[str]:
    """Запрос суперпользователем образа в db; строки ответа."""
    return compose(run, *SUPERUSER, stdin=sql.encode()).splitlines()


def answer_problems(found: dict[str, Any], user: str, wanted: list[str]) -> list[str]:
    """Расхождения ответа пробы: вход не удался, чужой `current_user`, не те коды команд —
    в причине только SQLSTATE."""
    if "connect" in found:
        return [f"вход — {found['connect']}"]
    problems = [f"current_user — {found.get('current_user')}, ждали {user}"] * (
        found.get("current_user") != user
    )
    codes = list(found.get("results", {}).values())
    if codes != wanted:
        runs = ", ".join(f"{code} × {len(list(same))}" for code, same in itertools.groupby(wanted))
        problems.append(f"коды команд {codes}, ждали {runs}")
    return problems


def row_problems(where: str, rows: list[str], wanted: list[str]) -> list[str]:
    """Строки ответа суперпользователя против ожидаемых по порядку; разница в числе — числом."""
    problems = [f"{where}: «{got}»" for got, want in zip(rows, wanted, strict=False) if got != want]
    return problems + [f"{where}: строк {len(rows)}, ждали {len(wanted)}"] * (
        len(rows) != len(wanted)
    )


def step_service_denied(run: Run) -> str:
    """A85, первая строка: проба в контейнере api — `current_user` `audit_app`, каждая команда
    схемы и `alembic_version` — `42501`; после пробы `alembic current` равен `alembic heads`."""
    problems = answer_problems(probe(run, "api", "service-denied"), APP, [DENIED] * 5)
    current, heads = alembic(run, "current"), alembic(run, "heads")
    problems += [f"alembic current {current}, heads {heads}"] * (not current or current != heads)
    if problems:
        raise StepError("; ".join(problems))
    return f"current_user {APP}; команд 5 — у каждой {DENIED}; alembic current = heads"


def step_migrate_role(run: Run) -> str:
    """A85, вторая строка: разовый migrate — `current_user` `audit_migrate`, создание и удаление
    таблицы в одной транзакции с откатом проходят; суперпользователем: владелец базы и
    `alembic_version` — `audit_migrate`, у обеих ролей вход есть, особых прав нет. Пароль роли
    миграций оракула — с «@», в адресе `%40`: migrate входит."""
    problems = answer_problems(probe(run, "migrate", "migrate-create"), MIGRATE, ["ok", "ok"])
    problems += row_problems("суперпользователем", superuser(run, OWNERS), OWNER_ROWS)
    if problems:
        raise StepError("; ".join(problems))
    return (
        f"current_user {MIGRATE}; создание и удаление в транзакции с откатом — ok; владелец базы "
        f"и alembic_version — {MIGRATE}; у обеих ролей вход, особых прав нет"
    )


def step_service_rights(run: Run) -> str:
    """A85, третья строка: роль миграций заводит `oracle_probe`; у роли сервиса INSERT без `id`,
    SELECT, UPDATE и DELETE проходят, TRUNCATE и DROP — `42501`; REFERENCES и TRIGGER — ложь;
    затем роль миграций удаляет таблицу — и после красной пробы api, — её больше нет."""
    made = answer_problems(probe(run, "migrate", "probe-make"), MIGRATE, ["ok"])
    if made:
        raise StepError("заведение: " + "; ".join(made))
    try:
        found = probe(run, "api", "service-rights")
        problems = answer_problems(found, APP, ["ok"] * 4 + [DENIED] * 2)
        problems += row_problems("права", superuser(run, PRIVILEGES), PRIVILEGE_ROWS)
    finally:
        dropped = answer_problems(probe(run, "migrate", "probe-drop"), MIGRATE, ["ok"])
    problems += [f"удаление: {problem}" for problem in dropped]
    problems += row_problems("после удаления", superuser(run, LEFT), ["oracle_probe 0"])
    if problems:
        raise StepError("; ".join(problems))
    return (
        f"у роли сервиса INSERT, SELECT, UPDATE, DELETE — ok, TRUNCATE и DROP — {DENIED}; "
        "REFERENCES и TRIGGER — ложь; oracle_probe удалена"
    )


#: Шаги ролей: примеры, название, функция, нужен ли он следующим шагам.
ROLE_STEPS: list[tuple[str, str, Callable[[Run], str], bool]] = [
    ("A85", "роль сервиса: отказ на схему и alembic_version", step_service_denied, False),
    ("A85", "роль миграций: владелец базы, без особых прав", step_migrate_role, False),
    ("A85", "роль сервиса: строки — да, таблица — нет", step_service_rights, False),
]
