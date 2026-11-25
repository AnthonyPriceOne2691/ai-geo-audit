"""Шаги оракула «Учёток-1» — роли базы на живом стеке: права роли сервиса и роли миграций
изнутри контейнеров процессов (A85), повтор скрипта ролей на поднятом томе, смена пароля, стенд
до поставки и снятие лишнего (A170), ошибка в скрипте ролей (A172, вторая строка). Проба базы —
`scripts/oracle_db_probe.py` на входе `python -` (L87); запросы суперпользователя — psql в
контейнере db через сокет.

Импорт в одну сторону: `stack_oracle.py` ставит эти шаги на живом стеке после шагов остановки
api и до A67: смена пароля в A170 на время шага отнимает у api вход в базу.
"""

from __future__ import annotations

import itertools
import json
import secrets
import subprocess
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from stack_oracle_compose import DB_URL, StepError, alembic, compose, compose_base, stack_env

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
LOGIN_REFUSED = "28P01"
#: Скрипт ролей в копии и в контейнере db; повтор — той же командой, что выкат на поднятом томе.
SCRIPT = "deploy/db/roles.sql"
RERUN = 'psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
RERUN += " -f /docker-entrypoint-initdb.d/roles.sql"
#: A172, вторая строка: psql с `-e` и копией скрипта на входе; лишнее слово — сразу после пароля
#: роли сервиса: ошибка разбора внутри транзакции.
ECHOED = 'psql -X -e -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
APP_ROLE_LINE = "PASSWORD :'app_password';"
BROKEN_ROLE_LINE = "PASSWORD :'app_password' oracle_extra_word;"
#: A170, первая строка: роли и атрибуты, членства, владельцы, права и права по умолчанию; списки
#: прав — по строке на право, сравниваются множествами.
SNAPSHOT = """
SELECT 'роль ' || rolname || ' login=' || rolcanlogin || ' super=' || rolsuper || ' createrole='
       || rolcreaterole || ' createdb=' || rolcreatedb || ' repl=' || rolreplication
       || ' bypassrls=' || rolbypassrls
  FROM pg_roles WHERE rolname LIKE 'audit%';
SELECT 'членство ' || pg_get_userbyid(roleid) || ' → ' || pg_get_userbyid(member)
  FROM pg_auth_members
 WHERE pg_get_userbyid(member) LIKE 'audit%' OR pg_get_userbyid(roleid) LIKE 'audit%';
SELECT 'база ' || datname || ' владелец ' || pg_get_userbyid(datdba) FROM pg_database
 WHERE datname = 'audit';
SELECT DISTINCT 'право базы ' || a::text FROM pg_database, unnest(datacl) a
 WHERE datname = 'audit';
SELECT 'схема public владелец ' || pg_get_userbyid(nspowner) FROM pg_namespace
 WHERE nspname = 'public';
SELECT DISTINCT 'право схемы ' || a::text FROM pg_namespace, unnest(nspacl) a
 WHERE nspname = 'public';
SELECT 'объект ' || c.relname || ' ' || c.relkind::text || ' владелец '
       || pg_get_userbyid(c.relowner)
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'S');
SELECT DISTINCT 'право объекта ' || c.relname || ' ' || a::text
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace, unnest(c.relacl) a
 WHERE n.nspname = 'public';
SELECT DISTINCT 'по умолчанию ' || pg_get_userbyid(d.defaclrole) || ' '
       || coalesce(n.nspname, '-') || ' ' || d.defaclobjtype::text || ' ' || a::text
  FROM pg_default_acl d LEFT JOIN pg_namespace n ON n.oid = d.defaclnamespace,
       unnest(d.defaclacl) a;
"""
#: A170, третья строка: таблица суперпользователя со строкой — база стенда до этой поставки.
LEGACY = """
CREATE TABLE oracle_legacy (id bigserial PRIMARY KEY);
INSERT INTO oracle_legacy DEFAULT VALUES;
"""
LEGACY_OWNERS = """
SELECT relname || ' ' || pg_get_userbyid(relowner) FROM pg_class
 WHERE relname IN ('oracle_legacy', 'oracle_legacy_id_seq') ORDER BY relname;
"""
LEGACY_ROWS = [f"oracle_legacy {MIGRATE}", f"oracle_legacy_id_seq {MIGRATE}"]
LEGACY_DROP = "DROP TABLE IF EXISTS oracle_legacy;"
#: A170, четвёртая строка: лишнее у роли сервиса — право, права по умолчанию, `CREATE`, членство.
EXTRA_GRANTS = """
GRANT TRUNCATE ON oracle_extra TO audit_app;
ALTER DEFAULT PRIVILEGES FOR ROLE audit_migrate IN SCHEMA public GRANT ALL ON TABLES TO audit_app;
GRANT CREATE ON SCHEMA public TO audit_app;
GRANT CREATE ON DATABASE audit TO audit_app;
GRANT audit_migrate TO audit_app;
"""
EXTRA_CHECK = """
SELECT 'TRUNCATE ' || has_table_privilege('audit_app', 'oracle_extra', 'TRUNCATE');
SELECT 'CREATE public ' || has_schema_privilege('audit_app', 'public', 'CREATE');
SELECT 'CREATE base ' || has_database_privilege('audit_app', 'audit', 'CREATE');
SELECT 'по умолчанию ' || v.t || ' ' || coalesce((
  SELECT string_agg(DISTINCT x.privilege_type, ',' ORDER BY x.privilege_type)
    FROM pg_default_acl d, aclexplode(d.defaclacl) x
   WHERE x.grantee = 'audit_app'::regrole AND d.defaclobjtype = v.t::"char"), '-')
  FROM (VALUES (1, 'r'), (2, 'S')) v(n, t) ORDER BY v.n;
SELECT 'членств ' || count(*) FROM pg_auth_members WHERE member = 'audit_app'::regrole;
"""
EXTRA_ROWS = [
    "TRUNCATE false",
    "CREATE public false",
    "CREATE base false",
    "по умолчанию r DELETE,INSERT,SELECT,UPDATE",
    "по умолчанию S SELECT,USAGE",
    "членств 0",
]


def compose_with(
    run: Run, extra: dict[str, str], *args: str, **kw: Any
) -> subprocess.CompletedProcess[bytes]:
    """Команда compose прогона с добавкой к окружению команды: значение `-e ИМЯ` — только там,
    не в строке команды (L84)."""
    return run.docker.result(*compose_base(run), *args, env=stack_env(run) | extra, **kw)


def probe(run: Run, where: str, name: str, url: str | None = None) -> dict[str, Any]:
    """Набор пробы базы в контейнере процесса — адресом процесса или `url` через
    `-e DATABASE_URL`; ответ — последняя строка вывода, JSON."""
    *head, service = WHERE[where]
    extra, flags = ({"DATABASE_URL": url}, ["-e", "DATABASE_URL"]) if url else ({}, [])
    script = (run.copy / PROBE).read_bytes()
    done = compose_with(run, extra, *head, *flags, service, "python", "-", name, stdin=script)
    lines = done.stdout.decode("utf-8", "replace").strip().splitlines()
    if done.returncode or not lines:
        tail = " | ".join(done.stderr.decode("utf-8", "replace").strip().splitlines()[-3:])
        raise StepError(f"проба {name} в {service} — код {done.returncode}: {tail or 'ответа нет'}")
    found: dict[str, Any] = json.loads(lines[-1])
    return found


def rerun(run: Run, extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[bytes]:
    """Скрипт ролей той же командой, что выкат на поднятом томе (A170); `extra` — переменные db на
    этот запуск: `exec -e ИМЯ`, значение — только в окружении команды (L84)."""
    names = [flag for name in extra or {} for flag in ("-e", name)]
    return compose_with(run, extra or {}, "exec", "-T", *names, "db", "sh", "-c", RERUN)


def code_problems(
    what: str, done: subprocess.CompletedProcess[bytes], wanted: int = 0
) -> list[str]:
    return [] if done.returncode == wanted else [f"{what} — код {done.returncode}, ждали {wanted}"]


def logins(run: Run) -> list[str]:
    """Обе роли входят своими адресами: проба `login` в api и в разовом migrate."""
    problems = [f"вход api: {p}" for p in answer_problems(probe(run, "api", "login"), APP, ["ok"])]
    found = probe(run, "migrate", "login")
    return problems + [f"вход migrate: {p}" for p in answer_problems(found, MIGRATE, ["ok"])]


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


def step_rerun(run: Run) -> str:
    """A170, первая строка: скрипт ролей второй раз той же командой, что выкат на поднятом томе, —
    код 0; снимок суперпользователем до и после равен множеством строк; обе роли входят своими
    адресами."""
    before = set(superuser(run, SNAPSHOT))
    done = rerun(run)
    after = set(superuser(run, SNAPSHOT))
    problems = code_problems("скрипт ролей", done)
    if before != after:
        problems.append(f"снимок: ушло {sorted(before - after)}, пришло {sorted(after - before)}")
    problems += logins(run)
    if problems:
        raise StepError("; ".join(problems))
    return f"скрипт ролей второй раз — код 0; снимок равен, строк {len(after)}; обе роли входят"


def step_new_password(run: Run) -> str:
    """A170, вторая строка: запуск с новым паролем роли сервиса (`exec -e
    POSTGRES_APP_PASSWORD`) — прежний адрес api получает `28P01`, адрес с новым паролем входит;
    затем запуск с прежним паролем — прежний адрес снова входит. Новый пароль — в маске до первой
    команды с ним; возврат идёт и после красной пробы."""
    new = secrets.token_hex(12)
    run.docker.mask.append(new)
    changed = rerun(run, {"POSTGRES_APP_PASSWORD": new})
    try:
        old = probe(run, "api", "login")
        fresh = probe(run, "api", "login", url=DB_URL.format(APP, new))
    finally:
        back = rerun(run)
    again = probe(run, "api", "login")
    problems = code_problems("запуск с новым паролем", changed)
    problems += code_problems("запуск с прежним паролем", back)
    if old != {"connect": LOGIN_REFUSED}:
        problems.append(f"прежний адрес после смены — {old.get('connect', 'вход прошёл')}")
    problems += [f"новый пароль: {p}" for p in answer_problems(fresh, APP, ["ok"])]
    problems += [f"прежний адрес после возврата: {p}" for p in answer_problems(again, APP, ["ok"])]
    if problems:
        raise StepError("; ".join(problems))
    return (
        f"новый пароль роли сервиса: прежний адрес — {LOGIN_REFUSED}, новый — вход; после "
        "запуска с прежним — прежний адрес снова входит"
    )


def step_legacy_table(run: Run) -> str:
    """A170, третья строка: база стенда до поставки — суперпользователь заводит `oracle_legacy` со
    строкой; после скрипта ролей владелец таблицы и её последовательности — `audit_migrate`, роль
    сервиса читает и пишет таблицу, роль миграций её удаляет. Таблица снимается и при отказе."""
    superuser(run, LEGACY)
    try:
        done = rerun(run)
        owners = superuser(run, LEGACY_OWNERS)
        used = probe(run, "api", "legacy")
    finally:
        dropped = probe(run, "migrate", "legacy-drop")
        superuser(run, LEGACY_DROP)
    problems = code_problems("скрипт ролей", done)
    problems += row_problems("владельцы", owners, LEGACY_ROWS)
    problems += [f"роль сервиса: {p}" for p in answer_problems(used, APP, ["ok", "ok"])]
    problems += [f"удаление: {p}" for p in answer_problems(dropped, MIGRATE, ["ok"])]
    if problems:
        raise StepError("; ".join(problems))
    return (
        f"oracle_legacy и её последовательность — {MIGRATE}; роль сервиса читает и пишет; роль "
        "миграций удалила таблицу"
    )


def step_extra_revoked(run: Run) -> str:
    """A170, четвёртая строка: роль миграций заводит `oracle_extra`, суперпользователь выдаёт роли
    сервиса лишнее; после скрипта ролей `TRUNCATE` из api — `42501`, лишних прав и членств нет,
    права по умолчанию — ровно четыре на таблицы и два на последовательности; роль миграций
    удаляет таблицу."""
    made = answer_problems(probe(run, "migrate", "extra-make"), MIGRATE, ["ok"])
    if made:
        raise StepError("заведение: " + "; ".join(made))
    try:
        superuser(run, EXTRA_GRANTS)
        done = rerun(run)
        truncated = probe(run, "api", "extra-truncate")
        rows = superuser(run, EXTRA_CHECK)
    finally:
        dropped = probe(run, "migrate", "extra-drop")
    problems = code_problems("скрипт ролей", done)
    problems += [f"роль сервиса: {p}" for p in answer_problems(truncated, APP, [DENIED])]
    problems += row_problems("лишнее", rows, EXTRA_ROWS)
    problems += [f"удаление: {p}" for p in answer_problems(dropped, MIGRATE, ["ok"])]
    if problems:
        raise StepError("; ".join(problems))
    return (
        f"лишнее снято: TRUNCATE — {DENIED}; CREATE на public и базу — нет; права по умолчанию — "
        "четыре и два; членств 0"
    )


def step_broken_script(run: Run) -> str:
    """A172, вторая строка: копия скрипта ролей с лишним словом сразу после пароля роли сервиса на
    входе `psql -e`, с новым паролем роли миграций (`exec -e POSTGRES_MIGRATE_PASSWORD`) — код 3;
    роль миграций входит прежним паролем: транзакция откатилась; ни пароля роли сервиса, ни
    прежнего, ни нового пароля роли миграций нет в сыром выводе psql — из сырого захвата — и в
    журнале db."""
    script = (run.copy / SCRIPT).read_text(encoding="utf-8")
    if script.count(APP_ROLE_LINE) != 1:
        raise StepError(f"в {SCRIPT} строка «{APP_ROLE_LINE}» — не одна")
    new = f"{secrets.token_hex(6)}@{secrets.token_hex(6)}"
    run.docker.mask.extend([new, new.replace("@", "%40")])
    broken = script.replace(APP_ROLE_LINE, BROKEN_ROLE_LINE).encode()
    extra = {"POSTGRES_MIGRATE_PASSWORD": new}
    head = ("exec", "-T", "-e", "POSTGRES_MIGRATE_PASSWORD", "db", "sh", "-c", ECHOED)
    done = compose_with(run, extra, *head, stdin=broken)
    said = run.docker.raw[-1][1]
    log = compose(run, "logs", "--no-color", "db", limit="docker")
    problems = code_problems("psql со скриптом с ошибкой", done, 3)
    login = answer_problems(probe(run, "migrate", "login"), MIGRATE, ["ok"])
    problems += [f"вход роли миграций прежним паролем: {p}" for p in login]
    hidden = (run.app_password, run.migrate_password, new)
    for where, text in (("сыром выводе psql", said), ("журнале db", log)):
        problems += [f"пароль роли в {where}"] * any(secret in text for secret in hidden)
    if problems:
        raise StepError("; ".join(problems))
    return (
        "psql — код 3; роль миграций входит прежним паролем; паролей ролей нет в сыром выводе psql "
        "и в журнале db"
    )


#: Шаги ролей: примеры, название, функция, нужен ли он следующим шагам.
ROLE_STEPS: list[tuple[str, str, Callable[[Run], str], bool]] = [
    ("A85", "роль сервиса: отказ на схему и alembic_version", step_service_denied, False),
    ("A85", "роль миграций: владелец базы, без особых прав", step_migrate_role, False),
    ("A85", "роль сервиса: строки — да, таблица — нет", step_service_rights, False),
    ("A170", "скрипт ролей второй раз: снимок прежний", step_rerun, False),
    ("A170", "новый пароль роли сервиса и возврат", step_new_password, False),
    ("A170", "таблицы стенда до поставки — роли миграций", step_legacy_table, False),
    ("A170", "лишнее у роли сервиса снимается", step_extra_revoked, False),
    ("A172", "ошибка в скрипте ролей: откат, паролей в выводе нет", step_broken_script, False),
]
