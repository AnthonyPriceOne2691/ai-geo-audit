"""Шаги ролей базы оракула без Docker (A85, три строки): проба базы на входе `python -` в
контейнере api и разовом migrate, запросы суперпользователя в db, судьи ответов; итог шага —
строкой целиком, у каждой ветки — свой тест (L88), флаги команд судятся (L84). Модуль
`scripts/stack_oracle_roles.py` лежит вне пакета."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import dbtools
import pytest

sys.path.insert(0, str(dbtools.ROOT / "scripts"))
roles = importlib.import_module("stack_oracle_roles")
PROBE_TEXT = b"# probe of the test\n"
API = ("exec", "-T", "api", "python", "-")
MIGRATE = ("run", "--rm", "-T", "--no-deps", "migrate", "python", "-")
DB: tuple[str, ...] = ("exec", "-T", "db", "psql", "-X", "-q", "-At", "-v", "ON_ERROR_STOP=1")
DB += ("-U", "audit", "-d", "audit")
DENIED = "42501"


def answer(user: str, *codes: str) -> str:
    """Строка пробы базы: `current_user` и коды команд набора по порядку."""
    return json.dumps({"current_user": user, "results": {f"c{i}": c for i, c in enumerate(codes)}})


class Stack:
    """Подставной compose: ответ пробы — по имени набора, ответ суперпользователя — по тексту
    запроса; журнал — аргументы и вход каждого вызова."""

    def __init__(self, probes: dict[str, str], rows: dict[str, list[str]]) -> None:
        self.probes, self.rows = probes, rows
        self.calls: list[tuple[tuple[str, ...], bytes | None]] = []

    def __call__(self, run: object, *args: str, **kw: Any) -> str:
        self.calls.append((args, kw.get("stdin")))
        if "python" in args:
            return f"строка до ответа\n{self.probes[args[-1]]}\n"
        return "".join(f"{row}\n" for row in self.rows[kw["stdin"].decode()])


@pytest.fixture
def run(tmp_path: Path) -> SimpleNamespace:
    """Копия прогона с файлом пробы базы — его байты идут на вход `python -`."""
    probe = tmp_path / roles.PROBE
    probe.parent.mkdir(parents=True)
    probe.write_bytes(PROBE_TEXT)
    return SimpleNamespace(copy=tmp_path)


def stack(
    monkeypatch: pytest.MonkeyPatch,
    probes: dict[str, str],
    rows: dict[str, list[str]] | None = None,
    revisions: tuple[list[str], list[str]] = (["abc123"], ["abc123"]),
) -> Stack:
    fake = Stack(probes, rows or {})
    monkeypatch.setattr(roles, "compose", fake)
    monkeypatch.setattr(roles, "alembic", lambda run, command: revisions[command == "heads"])
    return fake


def red(step: Any, run: SimpleNamespace) -> str:
    with pytest.raises(roles.StepError) as failure:
        step(run)
    return str(failure.value)


def test_a85_service_role_is_refused_every_command(
    monkeypatch: pytest.MonkeyPatch, run: SimpleNamespace
) -> None:
    """A85, первая строка: проба в контейнере api — `exec -T api python - service-denied`, вход —
    файл пробы; `current_user` — `audit_app`, каждая из пяти команд — `42501`; после пробы
    `alembic current` равен `alembic heads`. Итог — строкой целиком."""
    fake = stack(monkeypatch, {"service-denied": answer("audit_app", *[DENIED] * 5)})
    done = roles.step_service_denied(run)
    assert done == "current_user audit_app; команд 5 — у каждой 42501; alembic current = heads"
    assert fake.calls == [((*API, "service-denied"), PROBE_TEXT)]


@pytest.mark.parametrize(
    ("found", "why"),
    [
        (
            answer("audit_app", "ok", *[DENIED] * 4),
            "коды команд ['ok', '42501', '42501', '42501', '42501'], ждали 42501 × 5",
        ),
        (answer("audit_migrate", *[DENIED] * 5), "current_user — audit_migrate, ждали audit_app"),
        (json.dumps({"connect": "28P01"}), "вход — 28P01"),
        (answer("audit_app", *[DENIED] * 4), "коды команд ['42501', '42501', '42501', '42501']"),
    ],
    ids=["команда-прошла", "чужая-роль", "вход", "меньше-команд"],
)
def test_a85_service_role_step_is_red_on_any_other_answer(
    monkeypatch: pytest.MonkeyPatch, run: SimpleNamespace, found: str, why: str
) -> None:
    """A85, первая строка: прошедшая команда, чужой `current_user`, отказ входа и не те пять кодов
    — красное с причиной; кодов в причине нет ничего, кроме SQLSTATE."""
    stack(monkeypatch, {"service-denied": found})
    assert why in red(roles.step_service_denied, run)


def test_a85_service_role_step_is_red_when_the_revision_moved(
    monkeypatch: pytest.MonkeyPatch, run: SimpleNamespace
) -> None:
    """A85, первая строка: после пробы `alembic current` не равен `alembic heads` — красное."""
    probes = {"service-denied": answer("audit_app", *[DENIED] * 5)}
    stack(monkeypatch, probes, revisions=([], ["abc123"]))
    assert red(roles.step_service_denied, run) == "alembic current [], heads ['abc123']"


OWNER_ROWS = [
    "база audit_migrate",
    "alembic_version audit_migrate",
    "роль audit_app login=true super=false createrole=false createdb=false repl=false"
    " bypassrls=false",
    "роль audit_migrate login=true super=false createrole=false createdb=false repl=false"
    " bypassrls=false",
]


def test_a85_migrate_role_creates_in_a_rolled_back_transaction_and_owns_the_base(
    monkeypatch: pytest.MonkeyPatch, run: SimpleNamespace
) -> None:
    """A85, вторая строка: разовый migrate — `run --rm -T --no-deps migrate python -
    migrate-create`, `current_user` — `audit_migrate`, обе команды проходят; суперпользователем
    в db: владелец базы и `alembic_version` — `audit_migrate`, у обеих ролей вход есть, особых
    прав нет. Итог — строкой целиком."""
    probes = {"migrate-create": answer("audit_migrate", "ok", "ok")}
    fake = stack(monkeypatch, probes, {roles.OWNERS: OWNER_ROWS})
    done = roles.step_migrate_role(run)
    assert done == (
        "current_user audit_migrate; создание и удаление в транзакции с откатом — ok; владелец "
        "базы и alembic_version — audit_migrate; у обеих ролей вход, особых прав нет"
    )
    assert fake.calls == [
        ((*MIGRATE, "migrate-create"), PROBE_TEXT),
        (DB, roles.OWNERS.encode()),
    ]


@pytest.mark.parametrize(
    ("row", "changed"),
    [
        (0, "база audit"),
        (1, "alembic_version audit"),
        (
            3,
            "роль audit_migrate login=true super=true createrole=false createdb=false repl=false"
            " bypassrls=false",
        ),
        (2, "роль audit_app login=false super=false createrole=false createdb=false repl=false"),
    ],
    ids=["владелец-базы", "владелец-версии", "миграций-суперпользователь", "сервис-без-входа"],
)
def test_a85_migrate_role_step_names_each_wrong_row(
    monkeypatch: pytest.MonkeyPatch, run: SimpleNamespace, row: int, changed: str
) -> None:
    """A85, вторая строка: чужой владелец базы или `alembic_version`, роль миграций —
    суперпользователь, у роли сервиса нет входа — красное, причина называет строку снимка."""
    rows = [changed if i == row else r for i, r in enumerate(OWNER_ROWS)]
    stack(
        monkeypatch, {"migrate-create": answer("audit_migrate", "ok", "ok")}, {roles.OWNERS: rows}
    )
    assert red(roles.step_migrate_role, run) == f"суперпользователем: «{changed}»"


def test_a85_migrate_role_step_is_red_when_its_commands_fail(
    monkeypatch: pytest.MonkeyPatch, run: SimpleNamespace
) -> None:
    """A85, вторая строка: у роли миграций создание — `42501`, удаление — `25P02` — красное."""
    probes = {"migrate-create": answer("audit_migrate", DENIED, "25P02")}
    stack(monkeypatch, probes, {roles.OWNERS: OWNER_ROWS})
    assert red(roles.step_migrate_role, run) == "коды команд ['42501', '25P02'], ждали ok × 2"


RIGHTS = {
    "probe-make": answer("audit_migrate", "ok"),
    "service-rights": answer("audit_app", "ok", "ok", "ok", "ok", DENIED, DENIED),
    "probe-drop": answer("audit_migrate", "ok"),
}
PRIVILEGE_ROWS = ["REFERENCES false", "TRIGGER false"]


def rights_rows(privileges: list[str] = PRIVILEGE_ROWS, left: str = "0") -> dict[str, list[str]]:
    return {roles.PRIVILEGES: privileges, roles.LEFT: [f"oracle_probe {left}"]}


def test_a85_service_role_works_with_rows_and_not_with_the_table(
    monkeypatch: pytest.MonkeyPatch, run: SimpleNamespace
) -> None:
    """A85, третья строка: роль миграций заводит `oracle_probe`; у роли сервиса INSERT без `id`,
    SELECT, UPDATE и DELETE проходят, TRUNCATE и DROP — `42501`; REFERENCES и TRIGGER — ложь;
    роль миграций удаляет таблицу, её больше нет. Итог — строкой целиком, порядок вызовов —
    заведение, проба api, права, удаление, остаток."""
    fake = stack(monkeypatch, RIGHTS, rights_rows())
    done = roles.step_service_rights(run)
    assert done == (
        "у роли сервиса INSERT, SELECT, UPDATE, DELETE — ok, TRUNCATE и DROP — 42501; "
        "REFERENCES и TRIGGER — ложь; oracle_probe удалена"
    )
    assert fake.calls == [
        ((*MIGRATE, "probe-make"), PROBE_TEXT),
        ((*API, "service-rights"), PROBE_TEXT),
        (DB, roles.PRIVILEGES.encode()),
        ((*MIGRATE, "probe-drop"), PROBE_TEXT),
        (DB, roles.LEFT.encode()),
    ]


@pytest.mark.parametrize(
    ("change", "rows", "why"),
    [
        (
            {"service-rights": answer("audit_app", "ok", "ok", "ok", "ok", "ok", DENIED)},
            rights_rows(),
            "коды команд ['ok', 'ok', 'ok', 'ok', 'ok', '42501'], ждали ok × 4, 42501 × 2",
        ),
        (
            {"service-rights": answer("audit_app", DENIED, "ok", "ok", "ok", DENIED, DENIED)},
            rights_rows(),
            "коды команд ['42501', 'ok', 'ok', 'ok', '42501', '42501']",
        ),
        ({}, rights_rows(["REFERENCES true", "TRIGGER false"]), "права: «REFERENCES true»"),
        ({}, rights_rows(left="1"), "после удаления: «oracle_probe 1»"),
        (
            {"probe-drop": answer("audit_migrate", DENIED)},
            rights_rows(left="1"),
            "удаление: коды команд ['42501'], ждали ok × 1",
        ),
    ],
    ids=["truncate-прошёл", "insert-отказ", "references", "таблица-осталась", "удаление-отказ"],
)
def test_a85_service_rights_step_is_red_and_still_drops_the_table(
    monkeypatch: pytest.MonkeyPatch,
    run: SimpleNamespace,
    change: dict[str, str],
    rows: dict[str, list[str]],
    why: str,
) -> None:
    """A85, третья строка: TRUNCATE прошёл, INSERT получил отказ, у роли сервиса `REFERENCES`,
    таблица осталась, удаление не прошло — красное с причиной; удаление таблицы идёт и после
    красной пробы api."""
    fake = stack(monkeypatch, RIGHTS | change, rows)
    assert why in red(roles.step_service_rights, run)
    assert ((*MIGRATE, "probe-drop"), PROBE_TEXT) in fake.calls


def test_a85_service_rights_step_stops_when_the_table_is_not_made(
    monkeypatch: pytest.MonkeyPatch, run: SimpleNamespace
) -> None:
    """A85, третья строка: роль миграций не завела таблицу — красное сразу, без пробы api и без
    удаления."""
    fake = stack(monkeypatch, RIGHTS | {"probe-make": answer("audit_migrate", "42P07")}, {})
    reason = red(roles.step_service_rights, run)
    assert reason == "заведение: коды команд ['42P07'], ждали ok × 1"
    assert fake.calls == [((*MIGRATE, "probe-make"), PROBE_TEXT)]


def test_a85_role_steps_go_in_order_and_none_stops_the_rest() -> None:
    """A85: шаги ролей по порядку — отказы роли сервиса, роль миграций, права роли сервиса; ни
    один не нужен следующим: красный шаг ролей не останавливает остальные."""
    assert [(n, step.__name__, needed) for n, _, step, needed in roles.ROLE_STEPS] == [
        ("A85", "step_service_denied", False),
        ("A85", "step_migrate_role", False),
        ("A85", "step_service_rights", False),
    ]
