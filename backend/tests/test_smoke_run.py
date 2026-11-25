"""Сквозные проверки smoke (A370–A379): помощник `smoke_run` на подставных сценариях, замок копии,
остатки прошлого прогона, окружение, сбои, уборка и сигналы; настоящие S3 и S1 — на тестовом
Postgres. Контракт CI (A372, A376) — в `test_smoke_ci.py`.

Каждый тест — со своим `.smoke/` в `tmp_path` и своим именем базы: настоящий `run.sh` той же копии и
доли mutmut с набором не сталкиваются. Каталог пакета подпроцессов — тот, откуда пакет импортировал
процесс теста, как A55: под порчами это `backend/mutants/src`, и пакет процесса набора тест не
сверяет."""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import dbtools
import envguard
import mutation_db
import pytest
import smoke_run

import ai_geo_audit

ROOT = envguard.ROOT
SRC = Path(ai_geo_audit.__file__).resolve().parents[1]
RUN_SH = ROOT / "delivery" / "evals" / "smoke" / "run.sh"
TESTS = Path(__file__).resolve().parent
SLEEPER = (sys.executable, "-c", "import time; time.sleep(60)")
Action = Callable[[smoke_run.Run, float], None]


def make_run(tmp_path: Path, lines: list[str], **seconds: float) -> smoke_run.Run:
    """Прогон теста: `.smoke/` и база smoke — свои, от `tmp_path`; вывод — в `lines`."""
    state = tmp_path / ".smoke"
    state.mkdir(exist_ok=True)
    db = dbtools.named(f"{tmp_path}:smoke")
    return smoke_run.Run(tmp_path, state, db, SRC, say=lines.append, **seconds)


def scenario(name: str, action: Action, limit: float = 5) -> smoke_run.Scenario:
    return smoke_run.Scenario(name, limit, action)


def ok(_run: smoke_run.Run, _deadline: float) -> None:
    """Подставной зелёный сценарий."""


def noisy_failure(run: smoke_run.Run, _deadline: float) -> None:
    """Подставной отказ с длинным выводом: хвост — последние строки файла сценария."""
    run.log("S2").write_text("\n".join(f"строка {i}" for i in range(100)), encoding="utf-8")
    raise smoke_run.ScenarioError("подставной отказ")


def fake_package(root: Path) -> Path:
    """Путь пакета «из этой копии» для прогона с корнем `root`: сверка судит путь, а не файл."""
    return root / "backend" / "src" / "ai_geo_audit" / "__init__.py"


def closed_url() -> str:
    """Адрес сервера тестов на закрытом порту петли: Postgres «не отвечает»."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"postgresql+asyncpg://audit@127.0.0.1:{port}/postgres"


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def gone(pid: int, seconds: float = 5) -> bool:
    """Процесса нет — не позже `seconds`: сироту-зомби снимает init, а не тест."""
    deadline = time.monotonic() + seconds
    while alive(pid):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.05)
    return True


def appeared(path: Path, seconds: float = 10) -> None:
    """Ждать файла не дольше `seconds`: подставной процесс сообщает, что готов."""
    deadline = time.monotonic() + seconds
    while not path.exists():
        if time.monotonic() > deadline:
            raise TimeoutError(f"нет {path.name} за {seconds:g} с")
        time.sleep(0.02)


#: Текст `OSError` соединения с пропавшим Postgres — как у asyncpg.
LOST = "Connect call failed ('127.0.0.1', 5462)"


def postgres_gone_after(monkeypatch: pytest.MonkeyPatch, calls: int) -> None:
    """`drop` помощника: первые `calls` вызовов — настоящие, дальше Postgres «пропал» — каждый
    следующий падает `OSError`."""
    real = smoke_run.drop
    seen: list[str] = []

    async def drop(db: str) -> bool:
        seen.append(db)
        if len(seen) > calls:
            raise OSError(LOST)
        return await real(db)

    monkeypatch.setattr(smoke_run, "drop", drop)


def lost_connection(_run: smoke_run.Run, _deadline: float) -> None:
    """Подставной сценарий: Postgres пропал посреди сценария."""
    raise OSError(LOST)


def test_a375_readme_table_and_helper_agree_on_ids_and_limits() -> None:
    """A375: id и пределы таблицы README и помощника одни и те же; строка без исполнения,
    исполнение без строки и другой предел — расхождение."""
    helper = [(s.id, s.limit) for s in smoke_run.SCENARIOS]
    text = smoke_run.README.read_text(encoding="utf-8")
    row = next(line for line in text.splitlines() if line.startswith("| S1 |"))
    copies = {
        "строка без исполнения": text.replace(row, f"{row}\n{row.replace('| S1 |', '| S4 |')}"),
        "исполнение без строки": text.replace(f"{row}\n", ""),
        "другой предел": text.replace(row, row.replace("| 60 с |", "| 61 с |")),
    }
    assert smoke_run.table(text) == helper
    assert {
        case: smoke_run.table(copy) == helper for case, copy in copies.items()
    } == dict.fromkeys(copies, False)


def test_a375_zero_scenarios_and_all_skipped_are_red(tmp_path: Path) -> None:
    """A375: итог «сценариев N из M»; ноль засчитанных — красное: и без сценариев, и когда все
    пропущены."""
    lines: list[str] = []
    run = make_run(tmp_path, lines)
    codes = (
        smoke_run.runner(run, (), {}),
        smoke_run.runner(run, (scenario("S1", ok),), {"S1": "проба"}),
    )
    assert codes == (1, 1)
    assert lines == [
        "сценариев 0 из 0",
        "S1: пропущен — проба",
        "сценариев 0 из 1, пропущен S1: проба",
    ]


def test_a373_first_failure_stops_with_the_cause_inside_the_merge_guard_window(
    tmp_path: Path,
) -> None:
    """A373: отказ последнего сценария — код 1; строка «S<n>: <причина>» — в первых 15 строках
    вывода (окно отказа `merge_guard`) вместе с двумя строками до сценариев, за ней хвост файла
    сценария; следующий сценарий не идёт."""
    lines: list[str] = []
    run = make_run(tmp_path, lines)
    ran: list[str] = []
    scenarios = (
        scenario("S3", ok),
        scenario("S1", ok),
        scenario("S2", noisy_failure),
        scenario("S9", lambda _run, _deadline: ran.append("S9")),
    )
    code = smoke_run.runner(run, scenarios, {})
    cause = lines.index("S2: подставной отказ")
    tail = lines[cause + 1 : cause + 1 + smoke_run.TAIL_LINES]
    assert (code, ran, cause + 2 < 15) == (1, [], True)
    assert tail == [f"    строка {i}" for i in range(100 - smoke_run.TAIL_LINES, 100)]
    assert lines[-1] == "сценариев 2 из 4"


def test_a373_scenario_over_its_limit_is_red_and_its_group_is_gone(tmp_path: Path) -> None:
    """A373: сценарий дольше своего предела — «S<n>: предел N с»; его группа процессов снята."""
    lines: list[str] = []
    run = make_run(tmp_path, lines)
    marker = tmp_path / "pid"
    body = f"import os, time; open({str(marker)!r}, 'w').write(str(os.getpid()))"
    body += "; time.sleep(60)"

    def slow(run: smoke_run.Run, deadline: float) -> None:
        smoke_run.run_limited((sys.executable, "-c", body), run.log("S3"), deadline, os.environ)

    code = smoke_run.runner(run, (scenario("S3", slow, limit=1),), {})
    assert (code, "S3: предел 1 с" in lines, gone(int(marker.read_text()))) == (1, True, True)


TERM_DEFAULT = "import time; open({ready!r}, 'w').close(); time.sleep(60)"
TERM_EXITS = (
    "import signal, sys, time; signal.signal(signal.SIGTERM, lambda *_: sys.exit(0)); "
    "open({ready!r}, 'w').close(); time.sleep(60)"
)
TERM_IGNORED = (
    "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
    "open({ready!r}, 'w').close(); time.sleep(60)"
)


@pytest.mark.parametrize(
    ("body", "cause"),
    [
        (TERM_DEFAULT, None),
        (TERM_EXITS, "сервер вышел кодом 0, а не сигналом SIGTERM"),
        (TERM_IGNORED, "сервер не вышел за 1 с после SIGTERM — снят killpg"),
    ],
    ids=["signal", "exit-code", "ignored"],
)
def test_a371_stop_is_green_only_on_the_end_by_sigterm(
    tmp_path: Path, body: str, cause: str | None
) -> None:
    """A371: остановка — `SIGTERM` группе; зелёное — конец сигналом `SIGTERM` не позже срока, а
    выход кодом или `killpg` по сроку — красное с причиной; после — ни группы, ни файла группы."""
    run = make_run(tmp_path, [], stop_seconds=1)
    ready = tmp_path / "ready"
    server = smoke_run.launch(
        run, (sys.executable, "-c", body.format(ready=str(ready))), os.environ
    )
    try:
        appeared(ready)
        smoke_run.stop_server(run, server)
        found = None
    except smoke_run.ScenarioError as failure:
        found = str(failure)
    finally:
        smoke_run.halt(run)
    assert (found, gone(server.pid), (run.state / smoke_run.SERVER_FILE).exists()) == (
        cause,
        True,
        False,
    )


def test_a373_s1_busy_port_fails_at_once_not_by_the_deadline(tmp_path: Path) -> None:
    """A373: порт сервера занят — S1 красный сразу, как сервер вышел, с кодом выхода и хвостом
    вывода, а не по сроку готовности 15 с."""
    lines: list[str] = []
    run = make_run(tmp_path, lines)
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        began = time.monotonic()
        code = smoke_run.runner(
            run, (scenario("S1", lambda r, d: smoke_run.s1(r, d, port=port), limit=60),), {}
        )
        spent = time.monotonic() - began
    text = "\n".join(lines)
    # uvicorn 0.54 на занятом порте выходит кодом 3 (сбой старта; проба 2026-11-24): число — его.
    exited = [
        line for line in lines if re.fullmatch(r"S1: сервер вышел с кодом \d+ до готовности", line)
    ]
    assert (code, spent < smoke_run.READY_SECONDS, len(exited)) == (1, True, 1), text
    assert "address already in use" in text.lower()


def test_a377_package_of_another_copy_is_refused_and_a_link_in_the_path_passes(
    tmp_path: Path,
) -> None:
    """A377: пакет не из этой копии — «судится чужой пакет: <путь>»; путь через ссылку (дерево
    `merge_guard` в `mktemp -d`: на macOS `/var` — ссылка на `/private/var`) — сверка проходит:
    `resolve()` с обеих сторон."""
    real = tmp_path / "real"
    package = fake_package(real)
    package.parent.mkdir(parents=True)
    package.touch()
    link = tmp_path / "link"
    link.symlink_to(real)
    other = fake_package(tmp_path / "other")
    found = {
        "корень через ссылку": smoke_run.package_problem(link, package),
        "пакет через ссылку": smoke_run.package_problem(real, fake_package(link)),
        "чужой пакет": smoke_run.package_problem(real, other),
    }
    assert found == {
        "корень через ссылку": None,
        "пакет через ссылку": None,
        "чужой пакет": f"{smoke_run.FOREIGN}: {other.parent.resolve()}",
    }


def test_a377_postgres_down_before_scenarios_counts_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A377: Postgres недоступен до сценариев — код 1, строка «окружение: …» с командой подъёма;
    первая строка — путь пакета; ни один сценарий не засчитан."""
    monkeypatch.setenv("TEST_DATABASE_URL", closed_url())
    lines: list[str] = []
    ran: list[str] = []
    only = scenario("S3", lambda _run, _deadline: ran.append("S3"))
    code = smoke_run.smoke(make_run(tmp_path, lines), (only,), {}, fake_package(tmp_path))
    assert (code, ran, len(lines)) == (1, [], 2), lines
    assert lines[0] == f"пакет: {fake_package(tmp_path).parent.resolve()}"
    assert lines[1].startswith("окружение: Postgres не отвечает")
    assert lines[1].endswith(dbtools.START)


def test_a377_foreign_package_stops_the_run_before_scenarios(tmp_path: Path) -> None:
    """A377: прогон на пакете не из этой копии — код 1 «судится чужой пакет: <путь>» сразу после
    строки пути пакета; ни один сценарий не засчитан."""
    lines: list[str] = []
    ran: list[str] = []
    only = scenario("S3", lambda _run, _deadline: ran.append("S3"))
    other = fake_package(tmp_path / "other")
    code = smoke_run.smoke(make_run(tmp_path, lines), (only,), {}, other)
    where = other.parent.resolve()
    assert (code, ran, lines) == (1, [], [f"пакет: {where}", f"{smoke_run.FOREIGN}: {where}"])


def test_a376_skip_only_by_name_from_the_table_and_with_a_reason() -> None:
    """A376: пропуск — `--skip ID=причина`: id из таблицы и причина словами; чужой id, пустая
    причина или другой флаг — отказ до прогона."""
    reason = "набор прошёл шагом «Тесты с покрытием» этой джобы"
    found = smoke_run.parse_skip(["--skip", f"S2={reason}"], smoke_run.SCENARIOS)
    refused = {}
    for case, args in {
        "чужой id": ["--skip", "S9=проба"],
        "без причины": ["--skip", "S2= "],
        "другой флаг": ["--only", "S2=проба"],
        "без значения": ["--skip"],
    }.items():
        with pytest.raises(SystemExit) as stop:
            smoke_run.parse_skip(args, smoke_run.SCENARIOS)
        refused[case] = str(stop.value).startswith("run.sh [--skip ID=причина]")
    assert (found, refused) == ({"S2": reason}, dict.fromkeys(refused, True))


def test_a377_run_sh_without_backend_venv_refuses(tmp_path: Path) -> None:
    """A377: нет `backend/.venv` — `run.sh` выходит с кодом 1 строкой «окружение: …» до
    сценариев."""
    copy = tmp_path / "delivery" / "evals" / "smoke" / "run.sh"
    copy.parent.mkdir(parents=True)
    shutil.copy2(RUN_SH, copy)
    done = subprocess.run(
        ["bash", str(copy)], capture_output=True, text=True, timeout=30, check=False
    )
    said = (done.returncode, done.stdout.strip())
    assert said == (1, "окружение: нет backend/.venv — cd backend && uv sync --frozen")


def test_a377_uv_runs_only_with_no_sync() -> None:
    """A377: `uv run` в `run.sh` и помощнике — только с `--no-sync`: `uv run --frozen` в дереве
    `merge_guard` переписал бы `.pth` хозяина одолженного окружения."""
    texts = {path.name: path.read_text(encoding="utf-8") for path in (RUN_SH, smoke_run.HERE)}
    bare = {name: re.findall(r"uv run(?! --no-sync)", text) for name, text in texts.items()}
    assert {name: found for name, found in bare.items() if found} == {}
    assert smoke_run.S2_COMMAND[:3] == ("uv", "run", "--no-sync")


def test_a377_alembic_subprocess_gets_the_pythonpath_of_the_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A377: `PYTHONPATH`, который `run.sh` ставит набору S2, доходит до alembic тестов: под
    `merge_guard` тесты миграций идут на пакете дерева (решение ведущей 2026-11-23T14:19Z)."""
    monkeypatch.setenv("PYTHONPATH", "/srv/tree/backend/src")
    assert dbtools.alembic_env()["PYTHONPATH"] == "/srv/tree/backend/src"


def test_a378_second_run_refuses_while_the_first_holds_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A378: второй прогон в той же копии, пока идёт первый, — код 1 «smoke уже идёт в этой
    копии»; базы, группы и `.smoke/` не касается: снимок до и после (L85)."""
    monkeypatch.setenv("TEST_DATABASE_URL", closed_url())
    lines: list[str] = []
    run = make_run(tmp_path, lines)
    record = run.state / smoke_run.SERVER_FILE
    record.write_text("группа первого", encoding="utf-8")
    run.log("S1").write_text("вывод первого", encoding="utf-8")
    ran: list[str] = []
    only = scenario("S3", lambda _run, _deadline: ran.append("S3"))
    with (run.state / "lock").open("a", encoding="utf-8") as first:
        fcntl.flock(first, fcntl.LOCK_EX | fcntl.LOCK_NB)
        code = smoke_run.smoke(run, (only,), {}, fake_package(tmp_path))
    assert (code, ran) == (1, [])
    assert lines == [f"{smoke_run.BUSY}: замок {run.state / 'lock'} держит другой прогон"]
    files = (record.read_text(encoding="utf-8"), run.log("S1").read_text(encoding="utf-8"))
    assert files == ("группа первого", "вывод первого")


HOLDER = """
import os, sys, time
from pathlib import Path
sys.path.insert(0, {tests!r})
import smoke_run
run = smoke_run.Run(Path({root!r}), Path({state!r}), {db!r}, Path({src!r}), say=print)
with smoke_run.copy_lock(run.state):
    smoke_run.launch(run, {sleeper!r}, dict(os.environ))
    time.sleep(60)
"""


def hold_and_launch(run: smoke_run.Run) -> subprocess.Popen[bytes]:
    """Прогон-подставка: держит замок копии и запускает подставной сервер тем же путём, что S1."""
    script = HOLDER.format(
        tests=str(TESTS),
        root=str(run.root),
        state=str(run.state),
        db=run.db,
        src=str(run.src),
        sleeper=SLEEPER,
    )
    env = dict(os.environ) | {"PYTHONPATH": str(SRC)}
    return subprocess.Popen([sys.executable, "-c", script], env=env, start_new_session=True)


def kill_9_while_serving(run: smoke_run.Run) -> int:
    """`kill -9` прогона-подставки, пока жив его сервер; pid группы сервера — из файла группы."""
    holder = hold_and_launch(run)
    record = run.state / smoke_run.SERVER_FILE
    try:
        appeared(record)
        pgid = int(json.loads(record.read_text(encoding="utf-8"))["pgid"])
    finally:
        holder.kill()
        holder.wait()
    return pgid


def test_a379_group_left_by_kill_9_is_removed_and_the_lock_is_free(tmp_path: Path) -> None:
    """A379: `kill -9` прогона во время S1 — сервер в своей группе жив (снимок до, L85); следующий
    прогон берёт замок — сервер дескриптор замка не наследует — и снимает группу: «группа <pid>
    снята»; файла группы нет."""
    run = make_run(tmp_path, [])
    pgid = kill_9_while_serving(run)
    try:
        alive_before = alive(pgid)
        with smoke_run.copy_lock(run.state):
            notes = smoke_run.leftover_group(run.state)
        found = (alive_before, notes, gone(pgid), (run.state / smoke_run.SERVER_FILE).exists())
    finally:
        smoke_run.kill_group(pgid)
    assert found == (True, [f"группа {pgid} снята"], True, False)


def test_a379_stale_file_with_a_foreign_leader_leaves_the_group_alone(tmp_path: Path) -> None:
    """A379: файл, где лидер группы — чужой процесс (pid повторился, L97): группа не тронута,
    строка об этом, файл снят."""
    state = make_run(tmp_path, []).state
    stranger = subprocess.Popen(SLEEPER, start_new_session=True)
    try:
        leader = ["Mon Jan  1 00:00:00 2024", "python -m ai_geo_audit.api"]
        record = {"pgid": stranger.pid, "leader": leader}
        (state / smoke_run.SERVER_FILE).write_text(json.dumps(record), encoding="utf-8")
        notes = smoke_run.leftover_group(state)
        alive = stranger.poll() is None
    finally:
        stranger.kill()
        stranger.wait()
    expected = [f"группа {stranger.pid} — лидер не наш сервер (pid повторился), не тронута"]
    assert (notes, alive, (state / smoke_run.SERVER_FILE).exists()) == (expected, True, False)


def test_a379_stale_file_without_a_live_group_is_named_and_removed(tmp_path: Path) -> None:
    """A379: файл группы есть, а процесса уже нет (машину перезагрузили) — строка «группы <pid>
    уже нет», файл снят, сигналов никому."""
    state = make_run(tmp_path, []).state
    finished = subprocess.Popen((sys.executable, "-c", "pass"), start_new_session=True)
    finished.wait()
    record = {"pgid": finished.pid, "leader": ["Mon Jan  1 00:00:00 2024", "python"]}
    (state / smoke_run.SERVER_FILE).write_text(json.dumps(record), encoding="utf-8")
    notes = smoke_run.leftover_group(state)
    expected = [f"группы {finished.pid} уже нет"]
    assert (notes, (state / smoke_run.SERVER_FILE).exists()) == (expected, False)


def test_a379_next_run_names_the_group_and_the_base_left_by_kill_9(tmp_path: Path) -> None:
    """A379: после `kill -9` прогона во время S1 следующий прогон до сценариев снимает группу и
    базу smoke: «от прошлого прогона: группа <pid> снята, база снята»."""
    lines: list[str] = []
    run = make_run(tmp_path, lines)
    asyncio.run(smoke_run.recreate(run.db))
    pgid = kill_9_while_serving(run)
    try:
        code = smoke_run.smoke(run, (scenario("S3", ok),), {}, fake_package(tmp_path))
    finally:
        smoke_run.kill_group(pgid)
        left = asyncio.run(smoke_run.drop(run.db))
    assert (code, left, gone(pgid)) == (0, False, True), lines
    assert lines[1] == f"от прошлого прогона: группа {pgid} снята, база снята"


def test_a374_s3_exit_zero_without_a_revision_is_red(tmp_path: Path) -> None:
    """A374: копия S3 — команда выходит с кодом 0, а ревизии в базе нет (L28): S3 красный
    «ревизия базы не head»."""
    lines: list[str] = []
    run = make_run(tmp_path, lines)
    quiet = (sys.executable, "-c", "pass")
    try:
        code = smoke_run.runner(
            run, (scenario("S3", lambda r, d: smoke_run.s3(r, d, quiet), limit=60),), {}
        )
    finally:
        asyncio.run(smoke_run.drop(run.db))
    head = smoke_run.head_revision()
    assert (code, f"S3: ревизия базы не head: нет вместо {head}" in lines) == (1, True), lines


#: Копия S3: ставит в базе ревизию head и не создаёт ни одной таблицы схемы.
TABLELESS = """
import asyncio, os, sys
import asyncpg
url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
async def main():
    conn = await asyncpg.connect(url)
    try:
        await conn.execute("CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY)")
        await conn.execute("INSERT INTO alembic_version VALUES ($1)", sys.argv[1])
    finally:
        await conn.close()
asyncio.run(main())
"""


def test_a370_head_revision_without_schema_tables_is_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A370, копия: ревизия базы — head, а таблицы схемы нет — S3 красный «нет таблиц схемы»:
    судятся таблицы метаданных моделей, а не одна ревизия (L28). Метаданные — подставные, с одной
    таблицей, которой ревизия не создаёт: настоящие таблицы `alembic upgrade head` создал бы."""
    metadata = SimpleNamespace(tables={"smoke_probe": None})
    monkeypatch.setattr(
        smoke_run, "models", SimpleNamespace(Base=SimpleNamespace(metadata=metadata))
    )
    lines: list[str] = []
    run = make_run(tmp_path, lines)
    command = (sys.executable, "-c", TABLELESS, smoke_run.head_revision())
    try:
        code = smoke_run.runner(
            run, (scenario("S3", lambda r, d: smoke_run.s3(r, d, command), limit=60),), {}
        )
    finally:
        asyncio.run(smoke_run.drop(run.db))
    assert (code, "S3: нет таблиц схемы: smoke_probe" in lines) == (1, True), lines


def test_a370_s3_judges_the_settings_tables_too() -> None:
    """A370, L121: S3 судит все таблицы `models.Base.metadata`, и среди них — таблицы ключей и
    настроек. Проверка — в свежем процессе, как в `run.sh` (`PYTHONPATH` на `backend/src` корня):
    в процессе pytest `schema` уже импортирован модулями тестов, и тест здесь не держал бы ничего
    (находка 6 проверяющей). Подпроцесс — не часть прогона порч: под mutmut пакет `mutants/src`
    с батутами и `MUTANT_UNDER_TEST=stats` звали бы `config()` mutmut от `tests/`, и сбор
    статистики падал (CI на `a7a2211`); поэтому путь — исходный, а переменной mutmut нет."""
    tables_of = "sorted(smoke_run.models.Base.metadata.tables)"
    code = f"import json, smoke_run; print(json.dumps({tables_of}))"
    env = {name: value for name, value in os.environ.items() if name != mutation_db.MUTANT}
    run = subprocess.run(
        [sys.executable, "-c", code],
        cwd=TESTS,
        env=env | {"PYTHONPATH": str(ROOT / "backend" / "src")},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert run.returncode == 0, run.stderr[-1500:]
    tables = json.loads(run.stdout.strip().splitlines()[-1])
    assert {"service_keys", "setting_versions"} <= set(tables)


def test_a370_a371_real_s3_then_s1_on_a_fresh_base(tmp_path: Path) -> None:
    """A370, A371: S3 на пустой базе smoke — ревизия head и все таблицы `models.Base.metadata`;
    S1 на ней — проба `api.probe` код 0, конец сигналом `SIGTERM`; после — ни группы, ни файла
    группы, база — до сноса прогоном."""
    lines: list[str] = []
    run = make_run(tmp_path, lines)
    try:
        code = smoke_run.runner(run, smoke_run.SCENARIOS[:2], {})
    finally:
        dropped = asyncio.run(smoke_run.drop(run.db))
    assert (code, dropped, lines[-1]) == (0, True, "сценариев 2 из 2"), lines
    assert not (run.state / smoke_run.SERVER_FILE).exists()


def test_a372_suite_base_of_the_root_is_dropped_after_s2(tmp_path: Path) -> None:
    """A372: после S2 базы набора своего корня нет — и после зелёного набора, и после красного."""
    run = make_run(tmp_path, [])
    suite = dbtools.own_name(tmp_path)
    commands = {
        "зелёный": (sys.executable, "-c", "pass"),
        "красный": (sys.executable, "-c", "raise SystemExit(3)"),
    }
    found: dict[str, tuple[str, bool]] = {}
    for case, command in commands.items():
        asyncio.run(smoke_run.recreate(suite))
        try:
            smoke_run.s2(run, time.monotonic() + 30, command)
            outcome = "зелёный"
        except smoke_run.ScenarioError as failure:
            outcome = str(failure)
        found[case] = (outcome, asyncio.run(smoke_run.drop(suite)))
    assert found == {"зелёный": ("зелёный", False), "красный": ("набор — код 3", False)}


def test_a371_server_record_names_the_leader_of_its_group(tmp_path: Path) -> None:
    """A371, A379: файл группы — pid группы, время старта и команда лидера; лидер — сам сервер
    (`start_new_session`): сигнал группе доходит до него."""
    run = make_run(tmp_path, [])
    server = smoke_run.launch(run, SLEEPER, os.environ)
    try:
        record = json.loads((run.state / smoke_run.SERVER_FILE).read_text(encoding="utf-8"))
        found = (record["pgid"], os.getpgid(server.pid), record["leader"])
        expected = (server.pid, server.pid, smoke_run.identity(server.pid))
    finally:
        smoke_run.halt(run)
    assert found == expected


def test_a373_unexpected_failure_of_a_scenario_is_a_cause_line_in_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A373: непредусмотренный сбой сценария — Postgres пропал посреди S3 — код 1, строка «S3: сбой
    OSError: <текст>» в первых 15 строках, за ней хвост с трассировкой и итог; уборка падает тем же
    сбоем — своей строкой после итога и причину не заменяет."""
    postgres_gone_after(monkeypatch, 1)
    lines: list[str] = []
    only = (scenario("S3", lost_connection),)
    code = smoke_run.smoke(make_run(tmp_path, lines), only, {}, fake_package(tmp_path))
    cause = lines.index(f"S3: сбой OSError: {LOST}")
    ending = [f"    OSError: {LOST}", "сценариев 0 из 1", f"уборка: сбой OSError: {LOST}"]
    assert (code, cause < 15, lines[-3:]) == (1, True, ending), lines


def test_a370_failed_cleanup_after_green_scenarios_is_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A370: сценарии зелёные, а базу smoke не снести — Postgres пропал к уборке: код 1, строка
    «уборка: сбой <Тип>: <текст>» после итога."""
    postgres_gone_after(monkeypatch, 1)
    lines: list[str] = []
    code = smoke_run.smoke(
        make_run(tmp_path, lines), (scenario("S3", ok),), {}, fake_package(tmp_path)
    )
    ending = ["сценариев 1 из 1", f"уборка: сбой OSError: {LOST}"]
    assert (code, lines[-2:]) == (1, ending), lines


def test_a377_unexpected_failure_before_scenarios_is_a_cause_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A377: сбой до сценариев — Postgres пропал между проверкой и сносом базы прошлого прогона:
    код 1, второй строкой «до сценариев: сбой <Тип>: <текст>», ни один сценарий не засчитан."""
    postgres_gone_after(monkeypatch, 0)
    lines: list[str] = []
    ran: list[str] = []
    only = scenario("S3", lambda _run, _deadline: ran.append("S3"))
    code = smoke_run.smoke(make_run(tmp_path, lines), (only,), {}, fake_package(tmp_path))
    said = [f"до сценариев: сбой OSError: {LOST}", f"уборка: сбой OSError: {LOST}"]
    assert (code, ran, lines[1:]) == (1, [], said), lines


@pytest.mark.parametrize("red", [False, True], ids=["зелёный", "красный"])
def test_a370_smoke_base_made_by_a_scenario_is_gone_after_the_run(
    tmp_path: Path, red: bool
) -> None:
    """A370: базу smoke, которую создал сценарий, уборка прогона сносит — и после зелёного
    сценария, и после красного."""
    lines: list[str] = []
    run = make_run(tmp_path, lines)

    def makes_base(run: smoke_run.Run, _deadline: float) -> None:
        asyncio.run(smoke_run.recreate(run.db))
        if red:
            raise smoke_run.ScenarioError("подставной отказ")

    try:
        code = smoke_run.smoke(run, (scenario("S3", makes_base),), {}, fake_package(tmp_path))
    finally:
        left = asyncio.run(smoke_run.drop(run.db))
    assert (code, left) == (int(red), False), lines


def test_a371_signal_with_a_failed_cleanup_does_not_claim_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A371: SIGTERM посреди сценария, а уборка упала — код 143, строка сбоя уборки и «прерван
    сигналом 15» без слов «сняты»; обработчики сигналов после прогона — прежние."""
    postgres_gone_after(monkeypatch, 1)
    lines: list[str] = []

    def interrupted(_run: smoke_run.Run, _deadline: float) -> None:
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(5)

    handlers = (signal.SIGINT, signal.SIGTERM)
    before = [signal.getsignal(s) for s in handlers]
    only = (scenario("S3", interrupted),)
    code = smoke_run.smoke(make_run(tmp_path, lines), only, {}, fake_package(tmp_path))
    restored = [signal.getsignal(s) for s in handlers] == before
    ending = [f"уборка: сбой OSError: {LOST}", "прерван сигналом 15"]
    assert (code, lines[-2:], restored) == (143, ending, True), lines


#: Прогон-подставка: сценарий создаёт базу smoke, запускает подставной сервер тем же путём, что S1,
#: и спит; код выхода — код `smoke()`.
SIGNALLED = """
import asyncio, os, sys, time
from pathlib import Path
sys.path.insert(0, {tests!r})
import smoke_run

def sleeping(run, _deadline):
    asyncio.run(smoke_run.recreate(run.db))
    smoke_run.launch(run, {server!r}, dict(os.environ))
    time.sleep(60)

run = smoke_run.Run(Path({root!r}), Path({state!r}), {db!r}, Path({src!r}))
only = (smoke_run.Scenario("S1", 60.0, sleeping),)
raise SystemExit(smoke_run.smoke(run, only, {{}}, Path({package!r})))
"""

#: Подставной сервер: готов — файл `ready`; на `SIGTERM` — файл `term` и выход через 1 с: второй
#: сигнал тест шлёт, пока уборка ждёт сервер.
LINGERING = """
import signal, sys, time
def term(*_):
    open({term!r}, "w").close()
    time.sleep(1)
    sys.exit(0)
signal.signal(signal.SIGTERM, term)
open({ready!r}, "w").close()
time.sleep(60)
"""


def signalled(run: smoke_run.Run, tmp_path: Path) -> subprocess.Popen[str]:
    """Помощник подпроцессом со спящим сценарием S1 и подставным сервером."""
    ready, term = tmp_path / "ready", tmp_path / "term"
    server = (sys.executable, "-c", LINGERING.format(ready=str(ready), term=str(term)))
    script = SIGNALLED.format(
        tests=str(TESTS),
        root=str(run.root),
        state=str(run.state),
        db=run.db,
        src=str(run.src),
        server=server,
        package=str(fake_package(run.root)),
    )
    env = dict(os.environ) | {"PYTHONPATH": str(SRC)}
    return subprocess.Popen(
        [sys.executable, "-c", script],
        env=env,
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )


@pytest.mark.parametrize(
    ("first", "second"),
    [(signal.SIGTERM, signal.SIGINT), (signal.SIGINT, signal.SIGTERM)],
    ids=["SIGTERM", "SIGINT"],
)
def test_a371_signal_cleans_up_once_and_a_second_one_does_not_cut_it(
    tmp_path: Path, first: signal.Signals, second: signal.Signals
) -> None:
    """A371, A370: сигнал посреди сценария — код 128 + номер (143 у SIGTERM, 130 у SIGINT) и
    строка «прерван сигналом N: сервер и база smoke сняты»; второй сигнал, пока уборка ждёт сервер,
    её не рвёт и кода не меняет: группы сервера и базы smoke после нет."""
    run = make_run(tmp_path, [])
    helper = signalled(run, tmp_path)
    record = run.state / smoke_run.SERVER_FILE
    pgid = None
    try:
        appeared(tmp_path / "ready")
        appeared(record)
        pgid = int(json.loads(record.read_text(encoding="utf-8"))["pgid"])
        helper.send_signal(first)
        appeared(tmp_path / "term")
        helper.send_signal(second)
        out, _ = helper.communicate(timeout=30)
        stopped = gone(pgid)
    finally:
        helper.kill()
        helper.wait()
        if pgid is not None:
            smoke_run.kill_group(pgid)
        left = asyncio.run(smoke_run.drop(run.db))
    said = f"прерван сигналом {int(first)}: сервер и база smoke сняты"
    found = (helper.returncode, out.splitlines()[-1], out.count("прерван"), left, stopped)
    assert found == (128 + first, said, 1, False, True), out


def test_a370_smoke_base_name_is_its_own_and_a_link_gives_the_same_run(tmp_path: Path) -> None:
    """A370, семь вопросов п. 5: база smoke — своё имя от корня копии, отдельно от базы набора,
    шаблона и копий порч; корень через ссылку — тот же прогон (`resolve()`): тот же `.smoke/`, та
    же база, тот же каталог пакета."""
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    run, through = smoke_run.Run.of(real), smoke_run.Run.of(link)
    root = real.resolve()
    others = {
        "набора": dbtools.own_name(root),
        "шаблона": mutation_db.template_name(root),
        "копии порч": mutation_db.copy_name(os.getpid(), root),
    }
    assert smoke_run.TEST_NAME.fullmatch(run.db)
    assert {case: name for case, name in others.items() if name == run.db} == {}
    found = (through.root, through.state, through.db, through.src)
    assert found == (root, root / ".smoke", run.db, root / "backend" / "src")


@pytest.mark.parametrize("name", ["postgres", "audit", "audit_test_", "audit_test_0123456789x"])
def test_a370_bases_without_the_test_name_are_refused_before_a_connection(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A370, «Безопасность»: создание и снос — только `audit_test_` и 10 знаков; иное имя —
    `RefusedError` до соединения с сервером."""
    connections: list[object] = []
    monkeypatch.setattr(smoke_run.asyncpg, "connect", lambda *args, **_: connections.append(args))
    for action in (smoke_run.recreate, smoke_run.drop):
        with pytest.raises(dbtools.RefusedError, match="только audit_test_"):
            asyncio.run(action(name))
    assert connections == []


@pytest.mark.parametrize(
    ("body", "cause"),
    [
        ("raise SystemExit(1)", "S1: проба здоровья — код 1"),
        ("import time; time.sleep(60)", "S1: проба здоровья не кончилась за 6 с после старта"),
    ],
    ids=["код", "срок"],
)
def test_a371_probe_gives_zero_within_the_readiness_deadline_from_the_start(
    tmp_path: Path, body: str, cause: str
) -> None:
    """A371: сервер готов, а проба `api.probe` — не код 0 (например, порт ей не передан) — S1
    красный с кодом пробы; проба не кончилась к сроку готовности от старта сервера — красный сразу
    по этому сроку, а не по пределу сценария; сервера и файла группы после нет."""
    lines: list[str] = []
    run = make_run(tmp_path, lines, ready_seconds=6)
    probe = (sys.executable, "-c", body)
    asyncio.run(smoke_run.recreate(run.db))
    began = time.monotonic()
    try:
        code = smoke_run.runner(
            run, (scenario("S1", lambda r, d: smoke_run.s1(r, d, probe=probe), limit=60),), {}
        )
    finally:
        asyncio.run(smoke_run.drop(run.db))
    spent = time.monotonic() - began
    assert (code, cause in lines, spent < 15) == (1, True, True), (spent, lines)
    assert not (run.state / smoke_run.SERVER_FILE).exists()


#: Подставной интерпретатор `backend/.venv`: печатает свой pid, `PYTHONPATH` и аргументы.
FAKE_PYTHON = '#!/bin/sh\nprintf "%s\\n" "$$" "$PYTHONPATH" "$@"\n'


def test_a377_run_sh_execs_the_copy_python_with_the_resolved_src(tmp_path: Path) -> None:
    """A377: `run.sh` — `exec` интерпретатора `backend/.venv` копии (у `run.sh` и помощника один
    pid: сигналы доходят до уборки без посредника); `PYTHONPATH` — `backend/src` корня после
    `pwd -P`, и при запуске через ссылку; аргументы — как есть."""
    copy = tmp_path / "copy"
    script = copy / "delivery" / "evals" / "smoke" / "run.sh"
    script.parent.mkdir(parents=True)
    shutil.copy2(RUN_SH, script)
    python = copy / "backend" / ".venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(FAKE_PYTHON, encoding="utf-8")
    python.chmod(0o755)
    link = tmp_path / "link"
    link.symlink_to(copy)
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    root = copy.resolve()
    expected = [str(root / "backend" / "src"), str(root / "backend" / "tests" / "smoke_run.py")]
    found = {}
    for case, start in {"копия": copy, "ссылка": link}.items():
        shell = subprocess.Popen(
            ["bash", str(start / "delivery" / "evals" / "smoke" / "run.sh"), "--skip", "S2=x"],
            env=env,
            stdout=subprocess.PIPE,
            text=True,
        )
        out, _ = shell.communicate(timeout=30)
        said = out.splitlines()
        found[case] = (shell.returncode, said[:1] == [str(shell.pid)], said[1:])
    same = (0, True, [*expected, "--skip", "S2=x"])
    assert found == {"копия": same, "ссылка": same}


def test_a377_scenarios_run_their_subprocesses_on_the_package_dir_of_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A377: `PYTHONPATH` подпроцессов S3, S1 и S2 — каталог пакета прогона: в дереве
    `merge_guard` — `backend/src` дерева, а не пакет хозяина одолженного окружения. Подпроцессы
    подменены: окружение пишется, команда — пустая."""
    seen: dict[str, str | None] = {}
    spawn = smoke_run.spawn

    def recording(
        _command: tuple[str, ...], log: Path, env: dict[str, str]
    ) -> subprocess.Popen[bytes]:
        seen[log.stem] = env.get("PYTHONPATH")
        return spawn((sys.executable, "-c", "pass"), log, os.environ)

    monkeypatch.setattr(smoke_run, "spawn", recording)
    run = make_run(tmp_path, [])
    run.src = tmp_path / "tree" / "backend" / "src"
    try:
        for action in (smoke_run.s3, smoke_run.s1, smoke_run.s2):
            with contextlib.suppress(smoke_run.ScenarioError):
                action(run, time.monotonic() + 30)
    finally:
        asyncio.run(smoke_run.drop(run.db))
    assert seen == dict.fromkeys(("S3", "S1", "S2"), str(run.src))


def test_a379_leader_identity_does_not_depend_on_the_language_of_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A379, L97: время старта и команда лидера — одна строка при любом языке прогона: запись под
    одним `LC_ALL` и сверка под другим совпадают (проба второй проверяющей: C, ru_RU.UTF-8 и
    en_GB.UTF-8 давали три разные строки)."""
    server = subprocess.Popen(SLEEPER, start_new_session=True)
    found = []
    try:
        for language in ("C", "ru_RU.UTF-8", "en_GB.UTF-8"):
            monkeypatch.setenv("LC_ALL", language)
            monkeypatch.setenv("LANG", language)
            found.append(smoke_run.identity(server.pid))
    finally:
        server.kill()
        server.wait()
    assert found[0] is not None
    assert found == [found[0]] * 3
