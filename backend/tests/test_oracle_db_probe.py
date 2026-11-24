"""Проба базы оракула `scripts/oracle_db_probe.py` без Docker (A85, A170, A172; L31, L87): виды
наборов — каждая команда своей транзакцией с откатом, все одной с откатом, одной с фиксацией;
вывод — одна строка JSON с `current_user` и SQLSTATE по команде, без текста ошибки; отказ входа
— `{"connect": SQLSTATE}`. База — своя база тестов на сервере разработки, таблица проб снимается
после каждого теста."""

from __future__ import annotations

import ast
import importlib
import json
import os
import runpy
import secrets
import subprocess
import sys
from collections.abc import AsyncIterator

import dbtools
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine

sys.path.insert(0, str(dbtools.ROOT / "scripts"))
dbprobe = importlib.import_module("oracle_db_probe")
SCRIPT = dbtools.ROOT / "scripts" / "oracle_db_probe.py"
TABLE = "oracle_t"
USER = dbtools.server_url().username


@pytest.fixture
async def probe_table(engine: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    """Таблицы проб нет ни до теста, ни после него."""
    async with engine.begin() as conn:
        await conn.execute(text(f"DROP TABLE IF EXISTS {TABLE}"))
    yield engine
    async with engine.begin() as conn:
        await conn.execute(text(f"DROP TABLE IF EXISTS {TABLE}"))


async def exists(engine: AsyncEngine) -> bool:
    async with engine.connect() as conn:
        return (
            await conn.execute(text(f"SELECT to_regclass('public.{TABLE}')"))
        ).scalar() is not None


async def test_a85_each_command_is_its_own_transaction_rolled_back(
    monkeypatch: pytest.MonkeyPatch, probe_table: AsyncEngine
) -> None:
    """A85: вид `each` — каждая команда своей транзакцией с откатом: созданная таблица следующей
    команде не видна, ошибка одной команды — её SQLSTATE, остальные идут дальше; после набора
    таблицы нет."""
    commands = (f"CREATE TABLE {TABLE} (a int)", f"SELECT count(*) FROM {TABLE}", "SELECT 1/0")
    monkeypatch.setattr(dbprobe, "SETS", {"t": ("each", commands)})
    found = await dbprobe.probe("t", url=dbtools.own_url())
    expected = dict(zip(commands, ["ok", "42P01", "22012"], strict=True))
    assert found == {"current_user": USER, "results": expected}
    assert not await exists(probe_table)


async def test_a85_together_set_is_one_transaction_rolled_back(
    monkeypatch: pytest.MonkeyPatch, probe_table: AsyncEngine
) -> None:
    """A85, вторая строка: вид `together` — все команды одной транзакцией с откатом: после ошибки
    остальные — `25P02`; после набора таблицы нет."""
    commands = (f"CREATE TABLE {TABLE} (a int)", f"INSERT INTO {TABLE} VALUES (1)", "SELECT 1/0")
    commands += ("SELECT 1",)
    monkeypatch.setattr(dbprobe, "SETS", {"t": ("together", commands)})
    found = await dbprobe.probe("t", url=dbtools.own_url())
    expected = dict(zip(commands, ["ok", "ok", "22012", "25P02"], strict=True))
    assert found == {"current_user": USER, "results": expected}
    assert not await exists(probe_table)


async def test_a85_commit_set_keeps_its_work(
    monkeypatch: pytest.MonkeyPatch, probe_table: AsyncEngine
) -> None:
    """A85, третья строка: вид `commit` — создание и удаление таблиц проб фиксируются."""
    sets = {
        "make": ("commit", (f"CREATE TABLE {TABLE} (id bigserial PRIMARY KEY, note text)",)),
        "drop": ("commit", (f"DROP TABLE {TABLE}",)),
    }
    monkeypatch.setattr(dbprobe, "SETS", sets)
    made = await dbprobe.probe("make", url=dbtools.own_url())
    assert made["results"] == {sets["make"][1][0]: "ok"}
    assert await exists(probe_table)
    dropped = await dbprobe.probe("drop", url=dbtools.own_url())
    assert dropped["results"] == {f"DROP TABLE {TABLE}": "ok"}
    assert not await exists(probe_table)


async def test_a85_error_text_is_not_printed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A85 (L31): у отказа — только SQLSTATE: текста ошибки и значения из него в выводе нет."""
    commands = ("SELECT 'value-from-error-123'::int",)
    monkeypatch.setattr(dbprobe, "SETS", {"t": ("each", commands)})
    found = await dbprobe.probe("t", url=dbtools.own_url())
    assert found["results"] == {commands[0]: "22P02"}
    assert "invalid input" not in json.dumps(found)


async def test_a170_failed_login_gives_only_its_sqlstate() -> None:
    """A170, вторая строка: неверный пароль — `{"connect": "28P01"}`; пароля в выводе нет."""
    wrong = f"wrong-{secrets.token_hex(8)}"
    url = make_url(dbtools.own_url()).set(password=wrong).render_as_string(hide_password=False)
    found = await dbprobe.probe("login", url=url)
    assert found == {"connect": "28P01"}
    assert wrong not in json.dumps(found)


def run_stdin(name: str, url: str) -> subprocess.CompletedProcess[str]:
    """Как оракул: файл пробы на вход `python -`, имя набора — аргументом, адрес — окружением."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DATABASE_URL", "APP_"))}
    return subprocess.run(
        [sys.executable, "-", name],
        input=SCRIPT.read_text(encoding="utf-8"),
        capture_output=True,
        text=True,
        timeout=60,
        env=env | {"DATABASE_URL": url},
        check=False,
    )


def test_a85_probe_from_python_stdin_prints_one_json_line() -> None:
    """A85 (L123): `python - login` с адресом в окружении — код 0, ровно одна строка JSON;
    неизвестный набор — код 2, одна строка JSON без трассировки."""
    done = run_stdin("login", dbtools.own_url())
    assert done.returncode == 0, done.stderr[-800:]
    assert done.stdout.count("\n") == 1
    assert json.loads(done.stdout) == {"current_user": USER, "results": {"SELECT 1": "ok"}}
    unknown = run_stdin("no-such-set", dbtools.own_url())
    assert unknown.returncode == 2
    assert unknown.stdout.count("\n") == 1 and "error" in json.loads(unknown.stdout)
    assert "Traceback" not in unknown.stdout + unknown.stderr


#: Наборы пробы — команды строк A85 и A170 поимённо, по строке на набор (L37).
SETS = {
    "login": ("each", ("SELECT 1",)),
    "service-denied": (
        "each",
        (
            "CREATE TABLE oracle_x (a int)",
            "CREATE SCHEMA oracle_s",
            "ALTER TABLE alembic_version ADD COLUMN x int",
            "TRUNCATE alembic_version",
            "DROP TABLE alembic_version",
        ),
    ),
    "migrate-create": ("together", ("CREATE TABLE oracle_x (a int)", "DROP TABLE oracle_x")),
    "probe-make": ("commit", ("CREATE TABLE oracle_probe (id bigserial PRIMARY KEY, note text)",)),
    "service-rights": (
        "each",
        (
            "INSERT INTO oracle_probe (note) VALUES ('oracle')",
            "SELECT count(*) FROM oracle_probe",
            "UPDATE oracle_probe SET note = 'probe'",
            "DELETE FROM oracle_probe",
            "TRUNCATE oracle_probe",
            "DROP TABLE oracle_probe",
        ),
    ),
    "probe-drop": ("commit", ("DROP TABLE oracle_probe",)),
    "legacy": (
        "each",
        ("SELECT count(*) FROM oracle_legacy", "INSERT INTO oracle_legacy DEFAULT VALUES"),
    ),
    "legacy-drop": ("commit", ("DROP TABLE oracle_legacy",)),
    "extra-make": ("commit", ("CREATE TABLE oracle_extra (a int)",)),
    "extra-truncate": ("each", ("TRUNCATE oracle_extra",)),
    "extra-drop": ("commit", ("DROP TABLE oracle_extra",)),
}


def test_a85_a170_probe_sets_are_the_example_commands() -> None:
    """A85, все три строки, и A170, вторая — четвёртая (L37): наборы пробы — ровно команды строк
    примеров; вид у каждого — каждая своей транзакцией, вместе с откатом или с фиксацией."""
    assert dbprobe.SETS == SETS


def test_a85_probe_file_is_self_contained() -> None:
    """A85 (L87): файл идёт в `python -` контейнера — импортирует только стандартную библиотеку,
    SQLAlchemy и пакет сервиса, из `scripts/` — ничего."""
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    names |= {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    allowed = {"__future__", "asyncio", "json", "sys"}
    outside = {
        n for n in names if n not in allowed and not n.startswith(("sqlalchemy", "ai_geo_audit"))
    }
    assert outside == set()


def test_a85_main_in_process_prints_one_json_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A85 (L113): `main()` в процессе — неизвестный набор: код 2 и одна строка JSON с `error`;
    известный: код 0 и одна строка — ответ пробы как есть. Подпроцесс `python -` замер покрытия
    к файлу не привязывает."""
    assert dbprobe.main(["-", "no-such-set"]) == 2
    out = capsys.readouterr().out
    assert out.count("\n") == 1 and "error" in json.loads(out)

    async def answer(name: str, url: str | None = None) -> dict[str, object]:
        return {"current_user": "u", "results": {name: "ok"}}

    monkeypatch.setattr(dbprobe, "probe", answer)
    assert dbprobe.main(["-", "login"]) == 0
    assert capsys.readouterr().out == '{"current_user": "u", "results": {"login": "ok"}}\n'


def test_a85_probe_file_as_main_exits_with_the_code_of_main(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A85 (L113): файл как `__main__` выходит с кодом `main()` — неизвестный набор: 2."""
    monkeypatch.setattr(sys, "argv", ["-", "no-such-set"])
    with pytest.raises(SystemExit) as stop:
        runpy.run_path(str(SCRIPT), run_name="__main__")
    assert stop.value.code == 2
    assert "error" in json.loads(capsys.readouterr().out)
