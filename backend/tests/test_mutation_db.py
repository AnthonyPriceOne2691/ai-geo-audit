"""Базы прогона гейта мутаций (A281–A286): шаблон один раз на прогон, своя копия у каждой сессии
pytest под mutmut, уборка своего корня. Корень — временный каталог теста: шаблон и копии настоящего
прогона тест не задевает, даже когда сам идёт под гейтом в одном из четырёх потоков."""

from __future__ import annotations

import ast
import asyncio
import fcntl
import hashlib
import os
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import api_probes
import asyncpg
import conftest
import dbtools
import mutation_db
import pytest
import run_summary
from sqlalchemy.engine import make_url

NAME = r"audit_test_[0-9a-f]{10}"
TESTS = Path(__file__).parent
MUTANT = "ai_geo_audit.config.domains.x_parse__mutmut_3"


@pytest.fixture(autouse=True)
def keep_run_state() -> Iterator[None]:
    """Копия сессии и построенные шаблоны после теста — как до него, что бы в нём ни случилось."""
    active, built = list(dbtools.ACTIVE), dict(mutation_db.BUILT)
    yield
    dbtools.ACTIVE[:] = active
    mutation_db.BUILT.clear()
    mutation_db.BUILT.update(built)


def sha_name(key: str) -> str:
    return "audit_test_" + hashlib.sha256(key.encode()).hexdigest()[:10]


async def connect(name: str) -> asyncpg.Connection:
    return await asyncpg.connect(dbtools._raw(dbtools.server_url().set(database=name)))


async def labels() -> dict[str, str | None]:
    """Базы сервера и их метки `COMMENT ON DATABASE`."""
    server = await asyncpg.connect(dbtools._raw(dbtools.server_url()))
    try:
        found = await server.fetch(
            "SELECT datname, shobj_description(oid, 'pg_database') AS label FROM pg_database"
        )
    finally:
        await server.close()
    return {row["datname"]: row["label"] for row in found}


async def schema(name: str) -> tuple[str, list[str]]:
    """Ревизия alembic и таблицы схем `public` и `probe` базы `name`."""
    conn = await connect(name)
    try:
        revision = await conn.fetchval("SELECT version_num FROM alembic_version")
        found = await conn.fetch(
            "SELECT schemaname || '.' || tablename AS t FROM pg_tables"
            " WHERE schemaname IN ('public', 'probe') ORDER BY 1"
        )
    finally:
        await conn.close()
    return revision, [row["t"] for row in found]


def taken(lock: Path) -> bool:
    """Замок ОС на файле взят кем-то: второе открытие не берёт его без ожидания."""
    with lock.open("a", encoding="utf-8") as other:
        try:
            fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(other, fcntl.LOCK_UN)
        return False


def test_a281_template_and_copy_names_keep_the_format(tmp_path: Path) -> None:
    """A281, A12: шаблон и копии — `audit_test_` и 10 знаков sha256 строк «корень:template» и
    «корень:pid»; у разных pid и разных корней имена разные; базовое имя копии не меняется."""
    root = str(tmp_path.resolve())
    template = mutation_db.template_name(tmp_path)
    copies = {mutation_db.copy_name(pid, tmp_path) for pid in (1, 2, 3)}
    assert dbtools.own_name(tmp_path) == sha_name(root)
    assert template == sha_name(f"{root}:template")
    assert mutation_db.copy_name(7, tmp_path) == sha_name(f"{root}:7")
    assert len(copies | {template, dbtools.own_name(tmp_path)}) == 5
    assert all(re.fullmatch(NAME, name) for name in copies | {template})
    assert mutation_db.template_name(tmp_path / "other") != template


def test_a281_active_copy_is_the_database_everyone_sees(tmp_path: Path) -> None:
    """A281, A18: в сессии с копией `own_name()` без корня, адрес базы тестов и окружение alembic —
    копия; с явным корнем — базовое имя копии; после блока — как до него."""
    before = dbtools.own_name()
    copy = mutation_db.copy_name(11, tmp_path)
    with dbtools.active(copy):
        url = make_url(dbtools.own_url()).database
        env = make_url(dbtools.alembic_env()["DATABASE_URL"]).database
        seen = (dbtools.own_name(), url, env, dbtools.own_name(dbtools.ROOT))
    assert seen == (copy, copy, copy, sha_name(str(dbtools.ROOT.resolve())))
    assert dbtools.own_name() == before


async def test_a281_a9_copy_is_a_database_of_its_own_with_the_tokyo_default(
    tmp_path: Path,
) -> None:
    """A281, A9: копия шаблона — своя база: пояс по умолчанию Asia/Tokyo (`ALTER DATABASE … SET`
    в копию не переносится, его ставит клон), ревизия и пустая таблица проб — как у шаблона,
    метка — имя шаблона; уборка снимает и копию, и шаблон."""
    template = await mutation_db.build_template(tmp_path)
    copy = await mutation_db.clone(tmp_path, pid=1)
    try:
        conn = await connect(copy)
        try:
            zone = await conn.fetchval("SHOW timezone")
            revision = await conn.fetchval("SELECT version_num FROM alembic_version")
            rows = await conn.fetchval("SELECT count(*) FROM probe.probe")
        finally:
            await conn.close()
        label = (await labels())[copy]
    finally:
        swept = await mutation_db.sweep(tmp_path)
    assert (zone, revision, rows, label) == ("Asia/Tokyo", await dbtools.version(), 0, template)
    assert swept == [copy]
    assert {copy, template} & (await labels()).keys() == set()


async def test_a283_two_copies_do_not_see_each_others_rows(tmp_path: Path) -> None:
    """A283: две копии одного шаблона — строку, записанную в таблицу проб одной копии, другая не
    видит: поток не видит записей соседа."""
    await mutation_db.build_template(tmp_path)
    one = await mutation_db.clone(tmp_path, pid=1)
    two = await mutation_db.clone(tmp_path, pid=2)
    try:
        writer = await connect(one)
        try:
            await writer.execute("INSERT INTO probe.probe (key) VALUES ('a283-neighbour')")
            mine = await writer.fetchval("SELECT count(*) FROM probe.probe")
        finally:
            await writer.close()
        reader = await connect(two)
        try:
            theirs = await reader.fetchval("SELECT count(*) FROM probe.probe")
        finally:
            await reader.close()
    finally:
        await mutation_db.sweep(tmp_path)
    assert (mine, theirs) == (1, 0)


async def test_a284_template_stays_as_built_whatever_a_copy_does(tmp_path: Path) -> None:
    """A284: копия сносит таблицу проб и правит ревизию alembic — у шаблона после сноса копии та же
    ревизия и тот же состав таблиц."""
    template = await mutation_db.build_template(tmp_path)
    before = await schema(template)
    copy = await mutation_db.clone(tmp_path, pid=1)
    try:
        conn = await connect(copy)
        try:
            await conn.execute(
                "DROP TABLE probe.probe CASCADE; UPDATE alembic_version SET version_num = 'a284'"
            )
        finally:
            await conn.close()
        await mutation_db.drop(copy, tmp_path, pid=1)
        after = await schema(template)
    finally:
        await mutation_db.sweep(tmp_path)
    assert after == before
    assert before[0] == await dbtools.version()
    assert "probe.probe" in before[1]


def test_a286_cleanup_takes_only_its_own_leftovers(tmp_path: Path) -> None:
    """A286: уборка берёт только копии своего корня — имя по формату и метка своего шаблона; копия
    другого корня, база разработки, имена не по формату, базовая база копии и сам шаблон — нет."""
    template = mutation_db.template_name(tmp_path)
    own = mutation_db.copy_name(5, tmp_path)
    other = tmp_path / "other"
    rows = [
        (own, template),
        (mutation_db.copy_name(5, other), mutation_db.template_name(other)),
        ("audit", template),
        ("copy_of_audit_test_0123456789", template),
        ("audit_test_0123456789_x", template),
        (dbtools.own_name(tmp_path), None),
        (template, None),
    ]
    assert mutation_db.leftovers(rows, template) == [own]


async def test_a286_next_run_sweeps_a_broken_run_and_leaves_the_others(tmp_path: Path) -> None:
    """A286: оборванный прогон оставил шаблон и копию убитого ребёнка — постройка шаблона следующего
    прогона того же корня их снимает; шаблон и копия другого корня и база этой сессии целы."""
    other = tmp_path / "other"
    await mutation_db.build_template(tmp_path)
    killed = await mutation_db.clone(tmp_path, pid=101)
    await mutation_db.build_template(other)
    neighbour = await mutation_db.clone(other, pid=101)
    try:
        await mutation_db.build_template(tmp_path)
        seen = (await labels()).keys()
    finally:
        await mutation_db.sweep(tmp_path)
        await mutation_db.sweep(other)
    assert killed not in seen
    kept = {mutation_db.template_name(tmp_path), mutation_db.template_name(other), neighbour}
    assert kept | {dbtools.own_name()} <= seen


async def test_a12_drop_refuses_a_foreign_name_before_connecting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A12, A18: снос базы прогона — только своей копии и своего шаблона: база разработки, базовая
    база копии, копия другого корня и копия другого pid — отказ до соединения."""

    class ReachedError(Exception):
        """Снос дошёл до соединения с сервером."""

    async def reach(*_: object, **__: object) -> None:
        raise ReachedError

    monkeypatch.setattr(asyncpg, "connect", reach)
    foreign = [
        "audit",
        dbtools.own_name(tmp_path),
        mutation_db.copy_name(3, tmp_path / "other"),
        mutation_db.copy_name(4, tmp_path),
    ]
    for name in foreign:
        with pytest.raises(dbtools.RefusedError, match=f"«{name}» не тронута"):
            await mutation_db.drop(name, tmp_path, pid=3)
    for name in (mutation_db.template_name(tmp_path), mutation_db.copy_name(3, tmp_path)):
        with pytest.raises(ReachedError):
            await mutation_db.drop(name, tmp_path, pid=3)


def test_a285_failed_copy_marks_the_mutant_and_stops_its_session(tmp_path: Path) -> None:
    """A285: шаблона нет — клон отказывает: сессия порчи не идёт («гейт не просудил» с именем
    порчи), метка — файл с именем порчи и причиной; копия в сессии не числится."""
    run, before = tmp_path / "run", list(dbtools.ACTIVE)
    with pytest.raises(mutation_db.CopyError, match=f"гейт не просудил «{MUTANT}»"):
        mutation_db.session_start({"MUTANT_UNDER_TEST": MUTANT}, tmp_path, run)
    assert "копия базы не создана" in (run / "failed" / f"{MUTANT}.txt").read_text("utf-8")
    assert before == dbtools.ACTIVE


def test_a285_failed_drop_marks_the_mutant_and_keeps_the_session_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A285, A286: снос копии в конце сессии отказывает (сервер не отвечает) — исключения нет,
    исход сессии решают тесты; метка с именем порчи; копия живёт до уборки и уборкой снята."""
    run, environ = tmp_path / "run", {"MUTANT_UNDER_TEST": MUTANT}
    asyncio.run(mutation_db.build_template(tmp_path))
    try:
        mutation_db.session_start(environ, tmp_path, run)
        copy = dbtools.own_name()
        with monkeypatch.context() as patch:
            patch.setattr(dbtools, "server_url", lambda: make_url(api_probes.closed_port_url()))
            mutation_db.session_end(environ, tmp_path, run)
        left = copy in asyncio.run(labels())
    finally:
        swept = asyncio.run(mutation_db.sweep(tmp_path))
    assert "не снесена" in (run / "failed" / f"{MUTANT}.txt").read_text("utf-8")
    assert (left, swept, copy in dbtools.ACTIVE) == (True, [copy], False)


def test_a281_a286_run_of_sessions_leaves_no_databases_and_reports(tmp_path: Path) -> None:
    """A281, A286: статистика строит шаблон и ставит уборку на выход главного процесса; у каждой
    сессии своя копия, после сессии её нет; копия убитого ребёнка живёт до уборки в конце прогона;
    уборку, позванную не в главном процессе, ребёнок не делает. В итоге — наибольшее число копий
    разом и шаблон; после прогона копий нет, шаблон снят."""
    run, exits, seen = tmp_path / "run", [], []

    def at_exit(func: Callable[..., object], *args: Any) -> None:
        exits.append((func, args))

    for label in (mutation_db.STATS, "", "fail", MUTANT):
        environ = {"MUTANT_UNDER_TEST": label}
        mutation_db.session_start(environ, tmp_path, run, at_exit)
        seen.append(dbtools.own_name())
        mutation_db.session_end(environ, tmp_path, run)
    killed = asyncio.run(mutation_db.clone(tmp_path, pid=4242))
    mutation_db.journal(run, "start", killed, "m__mutmut_9")
    mutation_db.finish_run(os.getpid() + 1, tmp_path, run)
    kept = killed in asyncio.run(labels())
    [(func, args)] = exits
    func(*args)
    after = asyncio.run(labels())
    report = (run / "report.txt").read_text("utf-8")
    assert (func, args) == (mutation_db.finish_run, (os.getpid(), tmp_path, run))
    assert seen == [mutation_db.copy_name(None, tmp_path)] * 4
    assert kept
    assert {killed, mutation_db.template_name(tmp_path), *seen} & after.keys() == set()
    assert "копий разом — наибольшее 1, и шаблон; без сноса в своей сессии — 1" in report
    assert "после прогона копий 0, шаблон снят" in report


def test_a281_most_copies_at_once_come_from_the_journal() -> None:
    """A281: по журналу — наибольшее число копий разом; копия без сноса живёт до уборки и брошена;
    новый клон того же имени (pid убитого ребёнка) закрывает прежнюю; снос и клон в одно время —
    не две копии разом."""

    def line(at: float, event: str, name: str) -> str:
        return f"{at}\t{event}\t{name}\tm"

    four = [line(i, "start", f"c{i}") for i in range(4)]
    four += [line(10 + i, "end", f"c{i}") for i in range(4)]
    chain = [line(0, "start", "a"), line(1, "end", "a"), line(1, "start", "b"), line(2, "end", "b")]
    killed = [line(0, "start", "a"), line(1, "start", "b"), line(2, "end", "b")]
    killed.append(line(3, "start", "c"))
    reused = [line(0, "start", "a"), line(5, "start", "a"), line(6, "end", "a")]
    assert mutation_db.copies_at_once(four, until=20) == (4, 0)
    assert mutation_db.copies_at_once(chain, until=20) == (1, 0)
    assert mutation_db.copies_at_once(killed, until=5) == (2, 2)
    assert mutation_db.copies_at_once(reused, until=9) == (1, 1)


async def test_a283_base_database_is_prepared_under_an_os_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A283: подготовка базовой базы копии — под замком ОС на её имени: пока замок взят, второй не
    взять; `prepare()` держит его всё время `alembic upgrade head` — вложенные сессии и `prepare()`
    внутри теста из четырёх потоков не готовят одну базу разом."""
    monkeypatch.setattr(dbtools, "ROOT", tmp_path)
    monkeypatch.setattr(dbtools, "ACTIVE", [])
    name, real, during = dbtools.own_name(), dbtools.alembic, []
    lock = dbtools.lock_file(name)
    with dbtools.locked(name):
        held = taken(lock)
    free = taken(lock)

    def spy(*args: Any, **kwargs: Any) -> Any:
        during.append(taken(lock))
        return real(*args, **kwargs)

    monkeypatch.setattr(dbtools, "alembic", spy)
    try:
        await dbtools.prepare()
    finally:
        assert name == dbtools.own_name(tmp_path)
        server = await asyncpg.connect(dbtools._raw(dbtools.server_url()))
        try:
            await server.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        finally:
            await server.close()
        lock.unlink(missing_ok=True)
    assert (held, free, during) == (True, False, [True])


def test_a9_a11_connection_checks_judge_only_their_own_database() -> None:
    """A9, A11, A283: каждый запрос тестов к представлению соединений сервера судит только свою базу
    (`datname`) или своё соединение (`pg_backend_pid()`): в 4 потока поток не видит и не убивает
    соединений соседней копии."""
    found, loose = [], []
    for path in sorted(TESTS.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and "FROM pg_stat_activity" in str(node.value):
                found.append(f"{path.name}:{node.lineno}")
                if not re.search(r"datname|pg_backend_pid\(\)", node.value):
                    loose.append(found[-1])
    assert len(found) >= 7, found
    assert loose == []


def test_a106_session_end_is_chained_with_the_summary_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    """A106 (L63): итоговый хук сессии сцеплен — пропуск по-прежнему красит прогон, затем копия
    сессии под mutmut снесена."""
    ended: list[bool] = []
    monkeypatch.setattr(mutation_db, "session_end", lambda: ended.append(True))
    monkeypatch.setitem(run_summary.OUTCOME, "skipped", ["tests/test_x.py::test_y"])
    session = cast("pytest.Session", SimpleNamespace(exitstatus=pytest.ExitCode.OK))
    conftest.pytest_sessionfinish(session, pytest.ExitCode.OK)
    assert (session.exitstatus, ended) == (pytest.ExitCode.TESTS_FAILED, [True])


def test_a281_session_start_takes_the_copy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """A281: начало сессии pytest идёт через `mutation_db.session_start` — под mutmut копия
    шаблона, без него база рабочей копии."""
    started: list[bool] = []
    monkeypatch.setattr(mutation_db, "session_start", lambda: started.append(True))
    conftest.pytest_sessionstart(cast("pytest.Session", SimpleNamespace()))
    assert started == [True]


@pytest.mark.parametrize(
    ("environ", "deadline"),
    [({"MUTANT_UNDER_TEST": MUTANT}, True), ({"MUTANT_UNDER_TEST": ""}, True), ({}, False)],
    ids=["mutant", "clean-tests", "plain"],
)
def test_a282_property_profile_has_no_deadline_under_the_gate(
    environ: dict[str, str], deadline: bool
) -> None:
    """A282: под гейтом мутаций (переменная `MUTANT_UNDER_TEST` есть, у «clean tests» — пустая)
    у профиля свойств срока примера нет — сосед по CPU в 4 потока не даёт ложного «убит»; без
    гейта срок по умолчанию остаётся."""
    options = conftest.reproducible(environ)
    assert ("deadline" in options and options["deadline"] is None) is deadline
    assert {"derandomize": True, "database": None}.items() <= options.items()
