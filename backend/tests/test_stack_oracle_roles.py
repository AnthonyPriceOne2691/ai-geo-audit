"""Шаги ролей базы оракула без Docker (A85, три строки; A170, четыре; A380, первая; A172,
вторая): проба базы на входе `python -` в контейнере api и разовом migrate, запросы
суперпользователя в db, повтор скрипта ролей, судьи ответов; итог шага — строкой целиком, у
каждой ветки — свой тест (L88), флаги команд судятся (L84), значения `-e ИМЯ` — только в
окружении команды. Модули `scripts/` лежат вне пакета."""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import dbtools
import pytest

sys.path.insert(0, str(dbtools.ROOT / "scripts"))
roles = importlib.import_module("stack_oracle_roles")
docker_mod = importlib.import_module("stack_oracle_docker")
PROBE_TEXT = b"# probe of the test\n"
API = ("exec", "-T", "api", "python", "-")
MIGRATE = ("run", "--rm", "-T", "--no-deps", "migrate", "python", "-")
DB: tuple[str, ...] = ("exec", "-T", "db", "psql", "-X", "-q", "-At", "-v", "ON_ERROR_STOP=1")
DB += ("-U", "audit", "-d", "audit")
DENIED = "42501"
#: Скрипт ролей в копии прогона: строка с паролем роли сервиса — ровно одна (A172, вторая).
SCRIPT = "deploy/db/roles.sql"
APP_LINE = "PASSWORD :'app_password';"
SCRIPT_TEXT = f'\\set ECHO none\nALTER ROLE :"app_role" LOGIN\n  {APP_LINE}\nCOMMIT;\n'
#: Пароли ролей прогона; у роли миграций — с «@».
APP_VALUE, MIG_VALUE = "app-role-value-123456", "mig@role-value-123456"
#: Проба упала — не 0 и трассировка в stderr.
FAILED = None


def answer(user: str, *codes: str) -> str:
    """Строка пробы базы: `current_user` и коды команд набора по порядку."""
    return json.dumps({"current_user": user, "results": {f"c{i}": c for i, c in enumerate(codes)}})


def sticky(found: list[Any]) -> Any:
    """Ответы по очереди: последний остаётся на следующие вызовы."""
    return found.pop(0) if len(found) > 1 else found[0]


class Stack:
    """Подставной compose: ответ пробы — по имени набора (список — по очереди), ответ
    суперпользователя — по тексту запроса (список списков — по очереди), код psql скрипта ролей —
    по очереди из `codes`. Журнал — аргументы и вход каждого вызова, добавки к окружению и маска
    исполнителя в момент вызова; вывод `compose_with` — в сыром захвате исполнителя прогона."""

    def __init__(
        self,
        probes: dict[str, Any],
        rows: dict[str, Any],
        codes: tuple[int | str, ...] = (),
        psql: str = "",
        db_log: str = "",
    ) -> None:
        self.probes, self.rows = probes, rows
        self.codes, self.psql, self.db_log = list(codes), psql, db_log
        self.calls: list[tuple[tuple[str, ...], bytes | None]] = []
        self.extras: list[dict[str, str]] = []
        self.masks: list[list[str]] = []

    def answer(self, args: tuple[str, ...]) -> str | None:
        """Ответ пробы: по `сервис:набор`, иначе по набору."""
        service = "api" if "api" in args else "migrate"
        found = self.probes.get(f"{service}:{args[-1]}", self.probes.get(args[-1]))
        result: str | None = sticky(found) if isinstance(found, list) else found
        return result

    def __call__(self, run: object, *args: str, **kw: Any) -> str:
        self.calls.append((args, kw.get("stdin")))
        if "python" in args:
            return f"строка до ответа\n{self.answer(args)}\n"
        if "logs" in args:
            return self.db_log
        found = self.rows[kw["stdin"].decode()]
        if isinstance(found, Exception):  # запрос суперпользователя упал
            raise found
        rows = sticky(found) if found and isinstance(found[0], list) else found
        return "".join(f"{row}\n" for row in rows)

    def with_env(
        self, run: Any, extra: dict[str, str], *args: str, **kw: Any
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls.append((args, kw.get("stdin")))
        self.extras.append(dict(extra))
        self.masks.append(list(run.docker.mask))
        code, out, err = 0, self.psql, ""
        if "python" in args:
            found = self.answer(args)
            code, out = (
                (1, "") if found is FAILED else (0, found and f"строка до ответа\n{found}\n")
            )
            err = "Traceback\nboom\n" if found is FAILED else ""
        elif self.codes:
            code = self.codes.pop(0)
            if isinstance(code, str):  # команда не уложилась в предел — отказ исполнителя
                raise roles.StepError(code)
        run.docker.raw.append((args, out + err))
        return subprocess.CompletedProcess(args, code, out.encode(), err.encode())


@pytest.fixture
def run(tmp_path: Path) -> SimpleNamespace:
    """Копия прогона с файлом пробы базы — его байты идут на вход `python -` — и скриптом ролей;
    исполнитель с маской и сырым захватом, пароли ролей прогона."""
    probe = tmp_path / "scripts/oracle_db_probe.py"
    probe.parent.mkdir(parents=True)
    probe.write_bytes(PROBE_TEXT)
    script = tmp_path / SCRIPT
    script.parent.mkdir(parents=True)
    script.write_text(SCRIPT_TEXT, encoding="utf-8")
    docker = docker_mod.Docker(mask=[APP_VALUE, MIG_VALUE])
    return SimpleNamespace(
        copy=tmp_path, docker=docker, app_password=APP_VALUE, migrate_password=MIG_VALUE
    )


def stack(
    monkeypatch: pytest.MonkeyPatch,
    probes: dict[str, Any],
    rows: dict[str, Any] | None = None,
    revisions: tuple[list[str], list[str]] = (["abc123"], ["abc123"]),
    **psql: Any,
) -> Stack:
    fake = Stack(probes, rows or {}, **psql)
    monkeypatch.setattr(roles, "compose", fake)
    monkeypatch.setattr(roles, "compose_with", fake.with_env, raising=False)
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


RERUN = ("exec", "-T", "db", "sh", "-c")
RERUN_TEXT = 'psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
RERUN_TEXT += " -f /docker-entrypoint-initdb.d/roles.sql"
REFUSED = json.dumps({"connect": "28P01"})
LOGINS = {"api:login": answer("audit_app", "ok"), "migrate:login": answer("audit_migrate", "ok")}
SNAP = ["роль audit_app login=true", "право базы =Tc/audit_migrate"]


def test_a170_probe_without_an_answer_is_red(
    monkeypatch: pytest.MonkeyPatch, run: SimpleNamespace
) -> None:
    """A170 (L88): проба вышла с кодом 0, но строки ответа нет — красное с причиной."""
    stack(monkeypatch, {"api:login": ""})
    with pytest.raises(roles.StepError) as failure:
        roles.probe(run, "api", "login")
    assert str(failure.value) == "проба login в api — код 0: ответа нет"


def test_a170_script_rerun_keeps_the_snapshot_and_both_roles_log_in(
    monkeypatch: pytest.MonkeyPatch, run: SimpleNamespace
) -> None:
    """A170, первая строка: скрипт ролей второй раз — `exec -T db sh -c` с командой выката для
    поднятого тома, код 0; снимок суперпользователем до и после равен; обе роли входят своими
    адресами. Итог — строкой целиком."""
    fake = stack(monkeypatch, LOGINS, {roles.SNAPSHOT: SNAP})
    done = roles.step_rerun(run)
    assert done == "скрипт ролей второй раз — код 0; снимок равен, строк 2; обе роли входят"
    assert fake.calls == [
        (DB, roles.SNAPSHOT.encode()),
        ((*RERUN, RERUN_TEXT), None),
        (DB, roles.SNAPSHOT.encode()),
        ((*API, "login"), PROBE_TEXT),
        ((*MIGRATE, "login"), PROBE_TEXT),
    ]
    assert fake.extras == [{}, {}, {}]


@pytest.mark.parametrize(
    ("snapshots", "codes", "probes", "why"),
    [
        ([SNAP], (3,), LOGINS, "скрипт ролей — код 3, ждали 0"),
        (
            [SNAP, SNAP[:1]],
            (0,),
            LOGINS,
            "снимок: ушло ['право базы =Tc/audit_migrate'], пришло []",
        ),
        ([SNAP], (0,), LOGINS | {"migrate:login": REFUSED}, "вход migrate: вход — 28P01"),
        ([SNAP], (0,), LOGINS | {"api:login": REFUSED}, "вход api: вход — 28P01"),
    ],
    ids=["код", "снимок", "вход-migrate", "вход-api"],
)
def test_a170_rerun_step_is_red_on_code_snapshot_and_login(
    monkeypatch: pytest.MonkeyPatch,
    run: SimpleNamespace,
    snapshots: list[list[str]],
    codes: tuple[int, ...],
    probes: dict[str, str],
    why: str,
) -> None:
    """A170, первая строка: скрипт не 0 (порча «`CREATE ROLE` без проверки» — код 3), снимок
    изменился, роль не входит своим адресом — красное с причиной."""
    stack(monkeypatch, probes, {roles.SNAPSHOT: snapshots}, codes=codes)
    assert why in red(roles.step_rerun, run)


def changed_password(fake: Stack) -> str:
    """Новый пароль роли сервиса — из окружения первого запуска скрипта."""
    return fake.extras[0]["POSTGRES_APP_PASSWORD"]


def test_a170_new_service_password_refuses_the_old_address_and_comes_back(
    monkeypatch: pytest.MonkeyPatch, run: SimpleNamespace
) -> None:
    """A170, вторая строка: запуск с новым паролем роли сервиса — `exec -T -e
    POSTGRES_APP_PASSWORD`, значение только в окружении команды; прежний адрес api — `28P01`,
    адрес с новым паролем — `exec -T -e DATABASE_URL` — входит; запуск с прежним паролем — прежний
    адрес снова входит. Новый пароль — в маске до первой команды с ним. Итог — строкой целиком."""
    probes = {"api:login": [REFUSED, answer("audit_app", "ok"), answer("audit_app", "ok")]}
    fake = stack(monkeypatch, probes)
    done = roles.step_new_password(run)
    assert done == (
        "новый пароль роли сервиса: прежний адрес — 28P01, новый — вход; после запуска с "
        "прежним — прежний адрес снова входит"
    )
    new = changed_password(fake)
    assert len(new) >= 12 and new != APP_VALUE
    assert fake.calls == [
        (("exec", "-T", "-e", "POSTGRES_APP_PASSWORD", "db", "sh", "-c", RERUN_TEXT), None),
        ((*API, "login"), PROBE_TEXT),
        (("exec", "-T", "-e", "DATABASE_URL", "api", "python", "-", "login"), PROBE_TEXT),
        ((*RERUN, RERUN_TEXT), None),
        ((*API, "login"), PROBE_TEXT),
    ]
    assert fake.extras[2] == {"DATABASE_URL": f"postgresql+asyncpg://audit_app:{new}@db:5432/audit"}
    assert fake.extras[3] == {}
    assert new in fake.masks[0]
    assert not [args for args, _ in fake.calls if new in " ".join(args)]


@pytest.mark.parametrize(
    ("logins", "codes", "why"),
    [
        ([answer("audit_app", "ok")] * 3, (0, 0), "прежний адрес после смены — вход прошёл"),
        ([REFUSED, REFUSED, answer("audit_app", "ok")], (0, 0), "новый пароль: вход — 28P01"),
        (
            [REFUSED, answer("audit_app", "ok"), REFUSED],
            (0, 0),
            "прежний адрес после возврата: вход — 28P01",
        ),
        (
            [REFUSED, answer("audit_app", "ok"), answer("audit_app", "ok")],
            (3, 0),
            "запуск с новым паролем — код 3, ждали 0",
        ),
        (
            [REFUSED, answer("audit_app", "ok"), answer("audit_app", "ok")],
            (0, 3),
            "запуск с прежним паролем — код 3, ждали 0",
        ),
    ],
    ids=["прежний-входит", "новый-не-входит", "не-вернулся", "смена-код", "возврат-код"],
)
def test_a170_new_password_step_is_red_with_its_reason(
    monkeypatch: pytest.MonkeyPatch,
    run: SimpleNamespace,
    logins: list[str],
    codes: tuple[int, ...],
    why: str,
) -> None:
    """A170, вторая строка: прежний адрес входит после смены (порча «пароль ставится только при
    заведении роли»), новый не входит, прежний не вернулся, запуск не 0 — красное с причиной."""
    stack(monkeypatch, {"api:login": logins}, codes=codes)
    assert why in red(roles.step_new_password, run)


def test_a170_new_password_step_returns_the_old_password_after_a_failed_probe(
    monkeypatch: pytest.MonkeyPatch, run: SimpleNamespace
) -> None:
    """A170, вторая строка: проба новым адресом упала — красное с причиной пробы, а прежний пароль
    возвращён запуском скрипта: вход api не остаётся отнятым."""
    fake = stack(monkeypatch, {"api:login": [REFUSED, FAILED]})
    assert red(roles.step_new_password, run) == "проба login в api — код 1: Traceback | boom"
    assert fake.calls[-1] == ((*RERUN, RERUN_TEXT), None)


def test_a170_new_password_step_returns_the_old_password_when_the_change_fails(
    monkeypatch: pytest.MonkeyPatch, run: SimpleNamespace
) -> None:
    """A170, вторая строка: сама смена пароля не уложилась в предел — красное с причиной, а запуск
    с прежним паролем всё равно идёт: смена могла пройти в контейнере (замечание ведущей
    2026-11-25T08:42:08Z)."""
    late = "docker compose exec не уложился в 120 с"
    fake = stack(monkeypatch, {"api:login": REFUSED}, codes=(late, 0))
    assert red(roles.step_new_password, run) == late
    assert fake.calls[-1] == ((*RERUN, RERUN_TEXT), None)


LEGACY_PROBES = {
    "legacy": answer("audit_app", "ok", "ok"),
    "legacy-drop": answer("audit_migrate", "ok"),
}
LEGACY_OWNERS = ["oracle_legacy audit_migrate", "oracle_legacy_id_seq audit_migrate"]


def legacy_rows(owners: list[str] = LEGACY_OWNERS) -> dict[str, Any]:
    return {roles.LEGACY: [], roles.LEGACY_OWNERS: owners, roles.LEGACY_DROP: []}


def test_a170_legacy_table_goes_to_the_migrate_role(
    monkeypatch: pytest.MonkeyPatch, run: SimpleNamespace
) -> None:
    """A170, третья строка: суперпользователь заводит `oracle_legacy` со строкой; после скрипта
    ролей владелец таблицы и последовательности — `audit_migrate`; роль сервиса читает и пишет;
    роль миграций удаляет таблицу, суперпользователь снимает остаток. Итог — строкой целиком."""
    fake = stack(monkeypatch, LEGACY_PROBES, legacy_rows())
    done = roles.step_legacy_table(run)
    assert done == (
        "oracle_legacy и её последовательность — audit_migrate; роль сервиса читает и пишет; "
        "роль миграций удалила таблицу"
    )
    assert fake.calls == [
        (DB, roles.LEGACY.encode()),
        ((*RERUN, RERUN_TEXT), None),
        (DB, roles.LEGACY_OWNERS.encode()),
        ((*API, "legacy"), PROBE_TEXT),
        ((*MIGRATE, "legacy-drop"), PROBE_TEXT),
        (DB, roles.LEGACY_DROP.encode()),
    ]


@pytest.mark.parametrize(
    ("probes", "owners", "why"),
    [
        (
            LEGACY_PROBES,
            ["oracle_legacy audit", "oracle_legacy_id_seq audit"],
            "владельцы: «oracle_legacy audit»",
        ),
        (
            LEGACY_PROBES | {"legacy-drop": answer("audit_migrate", DENIED)},
            LEGACY_OWNERS,
            "удаление: коды команд ['42501'], ждали ok × 1",
        ),
        (
            LEGACY_PROBES | {"legacy": answer("audit_app", DENIED, DENIED)},
            LEGACY_OWNERS,
            "роль сервиса: коды команд ['42501', '42501'], ждали ok × 2",
        ),
    ],
    ids=["владелец-прежний", "удаление-отказ", "сервис-не-пишет"],
)
def test_a170_legacy_step_is_red_and_still_clears_the_table(
    monkeypatch: pytest.MonkeyPatch,
    run: SimpleNamespace,
    probes: dict[str, str],
    owners: list[str],
    why: str,
) -> None:
    """A170, третья строка: владелец прежний (порча «таблицы суперпользователя не передаются роли
    миграций»), удаление — отказ, роль сервиса не пишет — красное с причиной; таблица снимается и
    тогда: удаление ролью миграций и остаток суперпользователем."""
    fake = stack(monkeypatch, probes, legacy_rows(owners))
    assert why in red(roles.step_legacy_table, run)
    assert fake.calls[-2:] == [
        ((*MIGRATE, "legacy-drop"), PROBE_TEXT),
        (DB, roles.LEGACY_DROP.encode()),
    ]


def test_a170_legacy_step_clears_the_table_after_a_failed_probe(
    monkeypatch: pytest.MonkeyPatch, run: SimpleNamespace
) -> None:
    """A170, третья строка: проба api упала — красное с причиной пробы, таблица всё равно
    снимается."""
    fake = stack(monkeypatch, LEGACY_PROBES | {"legacy": FAILED}, legacy_rows())
    assert red(roles.step_legacy_table, run) == "проба legacy в api — код 1: Traceback | boom"
    assert fake.calls[-2:] == [
        ((*MIGRATE, "legacy-drop"), PROBE_TEXT),
        (DB, roles.LEGACY_DROP.encode()),
    ]


def test_a170_legacy_step_clears_the_table_when_making_it_fails(
    monkeypatch: pytest.MonkeyPatch, run: SimpleNamespace
) -> None:
    """A170, третья строка: заведение таблицы суперпользователем упало — таблица могла остаться
    без строки: красное с причиной, и таблица всё равно снимается."""
    failed = roles.StepError("docker compose — код 3: ERROR:  42P01")
    fake = stack(monkeypatch, LEGACY_PROBES, legacy_rows() | {roles.LEGACY: failed})
    assert red(roles.step_legacy_table, run) == "docker compose — код 3: ERROR:  42P01"
    assert fake.calls[-2:] == [
        ((*MIGRATE, "legacy-drop"), PROBE_TEXT),
        (DB, roles.LEGACY_DROP.encode()),
    ]


EXTRA_PROBES = {
    "extra-make": answer("audit_migrate", "ok"),
    "extra-truncate": answer("audit_app", DENIED),
    "extra-drop": answer("audit_migrate", "ok"),
}
EXTRA_ROWS = [
    "TRUNCATE false",
    "CREATE public false",
    "CREATE base false",
    "по умолчанию r DELETE,INSERT,SELECT,UPDATE",
    "по умолчанию S SELECT,USAGE",
    "членств 0",
]


def extra_rows(rows: list[str] = EXTRA_ROWS) -> dict[str, Any]:
    return {roles.EXTRA_GRANTS: [], roles.EXTRA_CHECK: rows}


def test_a170_extra_grants_are_revoked_by_the_script(
    monkeypatch: pytest.MonkeyPatch, run: SimpleNamespace
) -> None:
    """A170, четвёртая строка: роль миграций заводит `oracle_extra`, суперпользователь выдаёт роли
    сервиса лишнее, затем скрипт ролей; `TRUNCATE` из api — `42501`; лишних прав и членств нет,
    права по умолчанию — ровно четыре на таблицы и два на последовательности; роль миграций
    удаляет таблицу. Итог — строкой целиком."""
    fake = stack(monkeypatch, EXTRA_PROBES, extra_rows())
    done = roles.step_extra_revoked(run)
    assert done == (
        "лишнее снято: TRUNCATE — 42501; CREATE на public и базу — нет; права по умолчанию — "
        "четыре и два; членств 0"
    )
    assert fake.calls == [
        ((*MIGRATE, "extra-make"), PROBE_TEXT),
        (DB, roles.EXTRA_GRANTS.encode()),
        ((*RERUN, RERUN_TEXT), None),
        ((*API, "extra-truncate"), PROBE_TEXT),
        (DB, roles.EXTRA_CHECK.encode()),
        ((*MIGRATE, "extra-drop"), PROBE_TEXT),
    ]


@pytest.mark.parametrize(
    ("probes", "rows", "why"),
    [
        (
            EXTRA_PROBES | {"extra-truncate": answer("audit_app", "ok")},
            EXTRA_ROWS,
            "роль сервиса: коды команд ['ok'], ждали 42501 × 1",
        ),
        (EXTRA_PROBES, [*EXTRA_ROWS[:5], "членств 1"], "лишнее: «членств 1»"),
        (
            EXTRA_PROBES,
            [*EXTRA_ROWS[:3], "по умолчанию r ALL", *EXTRA_ROWS[4:]],
            "лишнее: «по умолчанию r ALL»",
        ),
        (
            EXTRA_PROBES | {"extra-drop": answer("audit_migrate", DENIED)},
            EXTRA_ROWS,
            "удаление: коды команд ['42501'], ждали ok × 1",
        ),
    ],
    ids=["truncate-прошёл", "членство", "права-по-умолчанию", "удаление-отказ"],
)
def test_a170_extra_step_is_red_and_still_drops_the_table(
    monkeypatch: pytest.MonkeyPatch,
    run: SimpleNamespace,
    probes: dict[str, str],
    rows: list[str],
    why: str,
) -> None:
    """A170, четвёртая строка: TRUNCATE прошёл, членство или права по умолчанию остались (порча
    «выдача без отзыва»), удаление — отказ — красное с причиной; таблица удаляется и тогда."""
    fake = stack(monkeypatch, probes, extra_rows(rows))
    assert why in red(roles.step_extra_revoked, run)
    assert fake.calls[-1] == ((*MIGRATE, "extra-drop"), PROBE_TEXT)


def test_a170_extra_step_stops_when_the_table_is_not_made(
    monkeypatch: pytest.MonkeyPatch, run: SimpleNamespace
) -> None:
    """A170, четвёртая строка: роль миграций не завела таблицу — красное сразу, без выдачи."""
    fake = stack(monkeypatch, EXTRA_PROBES | {"extra-make": answer("audit_migrate", DENIED)})
    assert red(roles.step_extra_revoked, run) == "заведение: коды команд ['42501'], ждали ok × 1"
    assert fake.calls == [((*MIGRATE, "extra-make"), PROBE_TEXT)]


PASSED_ROWS = ["членств 0", "права audit_app DELETE,INSERT,SELECT,UPDATE", "права PUBLIC 0"]
#: Лишнее на месте — скрипт упал до конца транзакции (порча «отзыв без `CASCADE`»).
KEPT_ROWS = [
    "членств 3",
    "права audit_app DELETE,INSERT,SELECT с передачей,UPDATE",
    "права PUBLIC 1",
]


def passed_rows(rows: list[str] | Exception = PASSED_ROWS) -> dict[str, Any]:
    return {roles.PASSED: rows if isinstance(rows, Exception) else [], roles.PASSED_CHECK: rows}


def test_a380_passed_extra_is_revoked_and_the_grantor_is_dropped(
    monkeypatch: pytest.MonkeyPatch, run: SimpleNamespace
) -> None:
    """A380, первая строка: суперпользователь заводит лишнее с передачей трёх видов, затем скрипт
    ролей той же командой, что в A170; членств у обеих ролей нет, у роли сервиса на
    `alembic_version` — четыре права без права передачи, у `PUBLIC` — ни одного; временная роль
    удаляется. Итог — строкой целиком."""
    fake = stack(monkeypatch, {}, passed_rows(), codes=(0, 0))
    assert roles.step_passed_extra(run) == (
        "скрипт ролей — код 0; членств 0; у роли сервиса на alembic_version — DELETE, INSERT, "
        "SELECT, UPDATE без права передачи, у PUBLIC — нет; oracle_grantor удалена"
    )
    assert fake.calls == [
        (DB, roles.PASSED.encode()),
        ((*RERUN, RERUN_TEXT), None),
        (DB, roles.PASSED_CHECK.encode()),
        (DB, b"DROP ROLE oracle_grantor;\n"),
    ]
    assert fake.extras == [{}, {}]


def test_a380_extra_is_passed_on_by_set_role_and_from_a_temporary_grantor() -> None:
    """A380, первая строка: лишнее трёх видов — право на `alembic_version` с правом передачи и от
    роли сервиса `PUBLIC`, членство с `ADMIN OPTION` и от роли сервиса роли миграций, членство от
    временной роли роли сервиса; выдаёт роль сервиса и временная роль — через `SET ROLE`."""
    assert roles.PASSED.split("\n") == [
        "",
        "GRANT SELECT ON alembic_version TO audit_app WITH GRANT OPTION;",
        "SET ROLE audit_app;",
        "GRANT SELECT ON alembic_version TO PUBLIC;",
        "RESET ROLE;",
        "GRANT pg_read_all_data TO audit_app WITH ADMIN OPTION;",
        "SET ROLE audit_app;",
        "GRANT pg_read_all_data TO audit_migrate;",
        "RESET ROLE;",
        "CREATE ROLE oracle_grantor;",
        "GRANT pg_write_all_data TO oracle_grantor WITH ADMIN OPTION;",
        "SET ROLE oracle_grantor;",
        "GRANT pg_write_all_data TO audit_app;",
        "RESET ROLE;",
        "",
    ]


@pytest.mark.parametrize(
    ("codes", "rows", "why"),
    [
        (
            (3, 3),
            KEPT_ROWS,
            "скрипт ролей — код 3, ждали 0; лишнее с передачей: «членств 3»; лишнее с передачей: "
            "«права audit_app DELETE,INSERT,SELECT с передачей,UPDATE»; лишнее с передачей: "
            "«права PUBLIC 1»; удаление oracle_grantor — код 3, ждали 0",
        ),
        (
            (0, 3),
            ["членств 1", *PASSED_ROWS[1:]],
            "лишнее с передачей: «членств 1»; удаление oracle_grantor — код 3, ждали 0",
        ),
        ((0, 0), [*PASSED_ROWS[:2], "права PUBLIC 1"], "лишнее с передачей: «права PUBLIC 1»"),
        (
            (0, 0),
            PASSED_ROWS[:2],
            "лишнее с передачей: строк 2, ждали 3",
        ),
    ],
    ids=["без-cascade", "без-granted-by", "public", "строк-меньше"],
)
def test_a380_step_is_red_with_its_reason(
    monkeypatch: pytest.MonkeyPatch,
    run: SimpleNamespace,
    codes: tuple[int, int],
    rows: list[str],
    why: str,
) -> None:
    """A380, первая строка: порчи «отзыв прав роли сервиса без `CASCADE`» — скрипт с кодом 3 и
    лишнее на месте — и «снятие членств без `GRANTED BY`» — членство от временной роли осталось, и
    она не удаляется; право `PUBLIC` осталось — красное с причиной целиком."""
    stack(monkeypatch, {}, passed_rows(rows), codes=codes)
    assert red(roles.step_passed_extra, run) == why


def test_a380_grantor_is_dropped_when_making_the_extra_fails(
    monkeypatch: pytest.MonkeyPatch, run: SimpleNamespace
) -> None:
    """A380, первая строка: суперпользователь не завёл лишнее — красное его причиной, скрипт ролей
    не идёт, временная роль удаляется и тогда."""
    failed = roles.StepError("docker exec — код 3: ERROR:  role exists")
    fake = stack(monkeypatch, {}, passed_rows(failed), codes=(0,))
    assert red(roles.step_passed_extra, run) == "docker exec — код 3: ERROR:  role exists"
    assert fake.calls == [(DB, roles.PASSED.encode()), (DB, b"DROP ROLE oracle_grantor;\n")]


ECHOED = 'psql -X -e -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
BROKEN = ("exec", "-T", "-e", "POSTGRES_MIGRATE_PASSWORD", "db", "sh", "-c", ECHOED)
BROKEN_TEXT = SCRIPT_TEXT.replace(APP_LINE, "PASSWORD :'app_password' oracle_extra_word;")
PSQL_ERROR = 'ERROR:  unrecognized role option "oracle_extra_word" at character 130\n'


def test_a172_broken_script_rolls_back_and_shows_no_role_password(
    monkeypatch: pytest.MonkeyPatch, run: SimpleNamespace
) -> None:
    """A172, вторая строка: копия скрипта ролей с лишним словом сразу после пароля роли сервиса —
    на вход `psql -e` через `exec -T -e POSTGRES_MIGRATE_PASSWORD`; код 3; роль миграций входит
    прежним паролем; паролей ролей нет ни в сыром выводе psql — из сырого захвата исполнителя, — ни
    в журнале db. Новый пароль роли миграций — с «@», в маске как есть и с `%40` до первой команды
    с ним. Итог — строкой целиком."""
    login = {"migrate:login": answer("audit_migrate", "ok")}
    fake = stack(monkeypatch, login, codes=(3,), psql=PSQL_ERROR, db_log=f"db-1  | {PSQL_ERROR}")
    done = roles.step_broken_script(run)
    assert done == (
        "psql — код 3; роль миграций входит прежним паролем; паролей ролей нет в сыром выводе "
        "psql и в журнале db"
    )
    new = fake.extras[0]["POSTGRES_MIGRATE_PASSWORD"]
    assert "@" in new and new != MIG_VALUE
    assert fake.calls == [
        (BROKEN, BROKEN_TEXT.encode()),
        (("logs", "--no-color", "db"), None),
        ((*MIGRATE, "login"), PROBE_TEXT),
    ]
    assert {new, new.replace("@", "%40")} <= set(fake.masks[0])


@pytest.mark.parametrize(
    ("codes", "psql", "db_log", "login", "why"),
    [
        ((0,), PSQL_ERROR, "", answer("audit_migrate", "ok"), "psql со скриптом с ошибкой — код 0"),
        (
            (3,),
            f"LINE 1: ...PASSWORD '{APP_VALUE}' oracle_extra_word\n",
            "",
            answer("audit_migrate", "ok"),
            "пароль роли в сыром выводе psql",
        ),
        (
            (3,),
            PSQL_ERROR,
            f"db-1  | STATEMENT: ALTER ROLE audit_migrate PASSWORD '{MIG_VALUE}'\n",
            answer("audit_migrate", "ok"),
            "пароль роли в журнале db",
        ),
        ((3,), PSQL_ERROR, "", REFUSED, "вход роли миграций прежним паролем: вход — 28P01"),
    ],
    ids=["код-0", "пароль-в-psql", "пароль-в-журнале", "вход-миграций"],
)
def test_a172_broken_script_step_is_red_with_its_reason(
    monkeypatch: pytest.MonkeyPatch,
    run: SimpleNamespace,
    codes: tuple[int, ...],
    psql: str,
    db_log: str,
    login: str,
    why: str,
) -> None:
    """A172, вторая строка: скрипт с ошибкой прошёл (порча «скрипт без транзакции» видна и входом),
    пароль роли в сыром выводе psql (порчи «снят `\\set ECHO none`», «снят `VERBOSITY terse`») или в
    журнале db (порча «снят `SET log_min_error_statement`»), роль миграций не входит прежним
    паролем — красное с причиной; пароля в причине нет."""
    stack(monkeypatch, {"migrate:login": login}, codes=codes, psql=psql, db_log=db_log)
    reason = red(roles.step_broken_script, run)
    assert why in reason
    assert APP_VALUE not in reason and MIG_VALUE not in reason


def test_a172_broken_script_step_needs_exactly_one_service_password_line(
    monkeypatch: pytest.MonkeyPatch, run: SimpleNamespace
) -> None:
    """A172, вторая строка: в скрипте ролей не одна строка с паролем роли сервиса — красное до
    первой команды: портить копию скрипта не на чем."""
    (run.copy / SCRIPT).write_text(SCRIPT_TEXT.replace(APP_LINE, ""), encoding="utf-8")
    fake = stack(monkeypatch, {})
    assert red(roles.step_broken_script, run) == (f"в {SCRIPT} строка «{APP_LINE}» — не одна")
    assert fake.calls == []


def test_a85_a170_a172_role_steps_go_in_order_and_none_stops_the_rest() -> None:
    """A85, A170, A380, A172: шаги ролей по порядку — три A85, четыре A170 (смена пароля — после
    шагов, которым нужен вход api по прежнему адресу), первая строка A380 — после шагов A170,
    вторая строка A172; ни один не нужен следующим: красный шаг ролей не останавливает
    остальные."""
    assert [(n, step.__name__, needed) for n, _, step, needed in roles.ROLE_STEPS] == [
        ("A85", "step_service_denied", False),
        ("A85", "step_migrate_role", False),
        ("A85", "step_service_rights", False),
        ("A170", "step_rerun", False),
        ("A170", "step_new_password", False),
        ("A170", "step_legacy_table", False),
        ("A170", "step_extra_revoked", False),
        ("A380", "step_passed_extra", False),
        ("A172", "step_broken_script", False),
    ]
