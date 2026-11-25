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
import subprocess
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
import redis
import redistools
import run_summary
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy.engine import make_url

NAME = r"audit_test_[0-9a-f]{10}"
TESTS = Path(__file__).parent
MUTANT = "ai_geo_audit.config.domains.x_parse__mutmut_3"
GATE = dbtools.ROOT / "scripts" / "lint" / "mutation_py.sh"
#: Представление соединений сервера: запрос к нему в тестах судит только свою базу.
VIEW = "pg_stat_activity"


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


async def test_a281_a286_killed_pid_copy_gives_way_to_a_fresh_one(tmp_path: Path) -> None:
    """A281, A286, находка проверяющей 2026-11-23T12:50Z (порча M8): копия ребёнка, убитого до
    сноса, ждёт уборки; новый ребёнок с тем же pid получает свежую копию шаблона под тем же
    именем — прежняя снесена клоном, строки убитого в ней нет, метка — имя шаблона. Без сноса клон
    упал бы на занятом имени, и порчу пометил бы ложный сбой копии вместо суда."""
    template = await mutation_db.build_template(tmp_path)
    killed = await mutation_db.clone(tmp_path, pid=101)
    try:
        conn = await connect(killed)
        try:
            await conn.execute("INSERT INTO probe.probe (key) VALUES ('a286-killed-pid')")
        finally:
            await conn.close()
        again = await mutation_db.clone(tmp_path, pid=101)
        conn = await connect(again)
        try:
            rows = await conn.fetchval("SELECT count(*) FROM probe.probe")
        finally:
            await conn.close()
        label = (await labels())[again]
    finally:
        await mutation_db.sweep(tmp_path)
    assert (again, rows, label) == (killed, 0, template)


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
        mutation_db.session_start(environ, tmp_path, run, at_exit, main=True)
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


def test_a287_self_check_run_builds_its_own_template(tmp_path: Path) -> None:
    """A287: прогон самопроверки — новый `mutmut run` со статистикой из кэша, первая сессия
    главного процесса — список тестов: она строит шаблон (шаблон прогона гейта уже снят его
    уборкой) и ставит свою уборку; копия ребёнка без шаблона — сбой, а не постройка."""
    run, exits = tmp_path / "run", []

    def at_exit(func: Callable[..., object], *args: Any) -> None:
        exits.append((func, args))

    for label in ("list_all_tests", "", MUTANT):
        environ = {"MUTANT_UNDER_TEST": label}
        mutation_db.session_start(environ, tmp_path, run, at_exit, main=True)
        mutation_db.session_end(environ, tmp_path, run)
    template = mutation_db.template_name(tmp_path)
    built = template in asyncio.run(labels())
    [(func, args)] = exits
    func(*args)
    assert (built, template in asyncio.run(labels())) == (True, False)
    assert not (run / "failed").exists()


def gate_block(mutants: Path) -> str:
    """Блок адаптации `mutation_py.sh` (метки и итог баз прогона) с заглушками тела гейта."""
    text = GATE.read_text(encoding="utf-8")
    start = text.index("# Адаптация (scripts/lint/adapted.json): у каждой сессии pytest")
    end = text.index("\ndb_marks\n", start) + len("\ndb_marks\n")
    stubs = f'mut_dir="{mutants}"; yellow=""; reset=""\n'
    stubs += 'not_judged() { echo "не судил"; exit 7; }\n'
    return stubs + text[start:end] + 'echo "дальше"\n'


@pytest.mark.parametrize("failed", [True, False], ids=["mark", "no-mark"])
def test_a285_a281_gate_reads_the_marks_and_prints_the_report(tmp_path: Path, failed: bool) -> None:
    """A285, A281: адаптация `mutation_py.sh` после прогона mutmut печатает итог уборки баз
    строкой; метка сбоя копии — «гейт не судит» с именем порчи и причиной и `not_judged`, а не
    убитая порча; без меток гейт идёт дальше."""
    runs = tmp_path / "mutants" / "db-copies"
    runs.mkdir(parents=True)
    (runs / "report.txt").write_text("базы гейта: копий разом — наибольшее 4, и шаблон\n", "utf-8")
    if failed:
        mutation_db.mark(runs, MUTANT, "копия базы не создана: OSError: нет сервера")
    script = gate_block(tmp_path / "mutants")
    done = subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False)
    assert "базы гейта: копий разом — наибольшее 4, и шаблон" in done.stdout
    if failed:
        assert (done.returncode, done.stdout.splitlines()[-1]) == (7, "не судил")
        assert "гейт не судит" in done.stderr
        assert f"{MUTANT} — копия базы не создана: OSError: нет сервера" in done.stderr
    else:
        assert (done.returncode, done.stdout.splitlines()[-1], done.stderr) == (0, "дальше", "")


def test_a286_nested_pytest_neither_builds_nor_sweeps(tmp_path: Path) -> None:
    """A286, A283: вложенный pytest, унаследовавший `MUTANT_UNDER_TEST=stats` от теста, — не процесс
    mutmut: шаблон не строит, уборку не ставит и копию родителя не сносит, а берёт свою копию."""
    run, exits = tmp_path / "run", []
    asyncio.run(mutation_db.build_template(tmp_path))
    parent = asyncio.run(mutation_db.clone(tmp_path, pid=1))
    environ = {"MUTANT_UNDER_TEST": mutation_db.STATS}
    try:
        mutation_db.session_start(environ, tmp_path, run, lambda *a: exits.append(a), main=False)
        own = dbtools.own_name()
        seen = asyncio.run(labels())
        mutation_db.session_end(environ, tmp_path, run)
        after = asyncio.run(labels())
    finally:
        asyncio.run(mutation_db.sweep(tmp_path))
    assert (own, exits) == (mutation_db.copy_name(None, tmp_path), [])
    assert {parent, own} <= seen.keys()
    assert parent in after and own not in after


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


#: Потоки прогона: у каждого — сессии подряд, (длительность, убита ли до сноса).
SLOTS = st.lists(
    st.lists(st.tuples(st.integers(1, 5), st.booleans()), max_size=5), min_size=1, max_size=4
)


@given(slots=SLOTS)
def test_a281_copies_at_once_stay_within_workers_plus_abandoned(
    slots: list[list[tuple[int, bool]]],
) -> None:
    """A281, слова владельца 2026-11-23T12:23Z и 12:27Z: наибольшее число копий разом — не больше
    числа потоков плюс копии «без сноса» (сессии, не дошедшие до сноса, — например, порчи, убитые
    по пределу времени; их копии снимает уборка); на журнале «4 потока, одна сессия без сноса»
    строка покажет 5 — граница, а не нарушение."""
    lines, until = [], 0
    for slot, sessions in enumerate(slots):
        moment = 0
        for number, (length, killed) in enumerate(sessions):
            lines.append(f"{moment}\tstart\tc{slot}-{number}\tm")
            if not killed:
                lines.append(f"{moment + length}\tend\tc{slot}-{number}\tm")
            moment += length
        until = max(until, moment + 1)
    journal = sorted(lines, key=lambda line: float(line.split("\t")[0]))
    most, abandoned = mutation_db.copies_at_once(journal, until)
    assert most <= len(slots) + abandoned
    four = [f"0\tstart\tc{i}\tm" for i in range(4)] + ["1\tstart\tc4\tm"]
    four += [f"{i + 1}\tend\tc{i}\tm" for i in range(1, 5)]
    assert mutation_db.copies_at_once(four, until=9) == (5, 1)


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
            query = node.value if isinstance(node, ast.Constant) else None
            if isinstance(query, str) and query.startswith("SELECT ") and VIEW in query:
                found.append(f"{path.name}:{node.lineno}")
                if not re.search(r"datname|pg_backend_pid\(\)", query):
                    loose.append(found[-1])
    assert len(found) >= 7, found
    assert loose == []


def test_a106_a281_summary_hook_stays_and_the_copy_ends_last(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A106 (L63), A281: хук итога подключён списком импорта, как был, — пропуск красит прогон;
    копию сессии под mutmut сносит `pytest_unconfigure`, после итога и развязки фикстур. База Redis
    сессии, в которой идёт сам тест, цела: конец сессии здесь — подменой (находка 10)."""
    ended: list[str] = []
    held = redistools.session_db()
    monkeypatch.setattr(mutation_db, "session_end", lambda: ended.append("base"))
    monkeypatch.setattr(redistools, "session_end", lambda: ended.append("redis"))
    conftest.pytest_unconfigure(cast("pytest.Config", SimpleNamespace()))
    assert ended == ["base", "redis"]
    assert redistools.session_db() == held
    assert conftest.pytest_sessionfinish is run_summary.pytest_sessionfinish


def test_a281_session_start_takes_the_copy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """A281: начало сессии pytest идёт через `mutation_db.session_start` — под mutmut копия
    шаблона, без него база рабочей копии; перед ней — база Redis. Второй базы Redis тест своему
    процессу не занимает: начало сессии здесь — подменой (находка 10)."""
    started: list[str] = []
    held = list(redistools.SESSION)
    monkeypatch.setattr(mutation_db, "session_start", lambda: started.append("base"))
    monkeypatch.setattr(redistools, "session_start", lambda: started.append("redis"))
    conftest.pytest_sessionstart(cast("pytest.Session", SimpleNamespace()))
    assert started == ["redis", "base"]
    assert held == redistools.SESSION


NO_DATABASE = redistools.NoFreeDatabaseError("свободной базы Redis для тестов нет: probe")


@pytest.mark.parametrize("ours", [True, False], ids=["mutmut", "nested"])
@pytest.mark.parametrize(
    ("server", "failure", "reason"),
    [
        ("postgres", None, "Postgres для тестов не отвечает (probe). Поднять: " + dbtools.START),
        (
            "redis",
            redis.ConnectionError("probe"),
            "Redis для тестов не отвечает (ConnectionError: ",
        ),
        ("redis", NO_DATABASE, str(NO_DATABASE)),
    ],
    ids=["postgres", "redis", "no-database"],
)
def test_a285_session_without_a_server_is_not_judged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ours: bool,
    server: str,
    failure: Exception | None,
    reason: str,
) -> None:
    """A285 (находка 12): в начале сессии порчи не ответил Postgres или Redis либо заняты все базы
    Redis — отказ прежней строкой, а в процессе mutmut ещё метка с именем порчи: гейт не судит, а не
    «убит». Вложенный pytest с унаследованной `MUTANT_UNDER_TEST` метки не кладёт: такие тесты
    нарочно поднимают его без сервера."""

    async def silent() -> str | None:
        return "probe" if server == "postgres" else None

    def refused(*args: object, **kwargs: object) -> None:
        assert failure is not None
        raise failure

    monkeypatch.setenv(mutation_db.MUTANT, MUTANT)
    monkeypatch.setattr(mutation_db, "RUN_DIR", tmp_path)
    monkeypatch.setattr(mutation_db, "mutmut_process", lambda: ours)
    monkeypatch.setattr(dbtools, "unreachable", silent)
    monkeypatch.setattr(redistools, "claim", refused)
    with pytest.raises(pytest.UsageError) as caught:
        conftest.pytest_sessionstart(cast("pytest.Session", SimpleNamespace()))
    assert str(caught.value).startswith(reason)
    marks = {path.name: path.read_text("utf-8") for path in tmp_path.rglob("*.txt")}
    assert marks == ({f"{MUTANT}.txt": f"{caught.value}\n"} if ours else {})


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
