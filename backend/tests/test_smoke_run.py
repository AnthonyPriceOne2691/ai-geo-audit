"""Сквозные проверки smoke (A370–A379): помощник `smoke_run` на подставных сценариях, замок копии,
остатки прошлого прогона и окружение; настоящие S3 и S1 — на тестовом Postgres.

Каждый тест — со своим `.smoke/` в `tmp_path` и своим именем базы: настоящий `run.sh` той же копии и
доли mutmut с набором не сталкиваются. Каталог пакета подпроцессов — тот, откуда пакет импортировал
процесс теста, как A55: под порчами это `backend/mutants/src`, и пакет процесса набора тест не
сверяет."""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import dbtools
import envguard
import pytest
import smoke_run
import yaml

import ai_geo_audit

ROOT = envguard.ROOT
SRC = Path(ai_geo_audit.__file__).resolve().parents[1]
RUN_SH = ROOT / "delivery" / "evals" / "smoke" / "run.sh"
QUALITY = ROOT / ".github" / "workflows" / "quality.yml"
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
    raise smoke_run.Failed("подставной отказ")


def fake_package(root: Path) -> Path:
    """Путь пакета «из этой копии» для прогона с корнем `root`: сверка судит путь, а не файл."""
    return root / "backend" / "src" / "ai_geo_audit" / "__init__.py"


def closed_url() -> str:
    """Адрес сервера тестов на закрытом порту петли: Postgres «не отвечает»."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"postgresql+asyncpg://audit@127.0.0.1:{port}/postgres"


def gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def appeared(path: Path, seconds: float = 10) -> None:
    """Ждать файла не дольше `seconds`: подставной процесс сообщает, что готов."""
    deadline = time.monotonic() + seconds
    while not path.exists():
        assert time.monotonic() < deadline, f"нет {path.name} за {seconds:g} с"
        time.sleep(0.02)


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
    except smoke_run.Failed as failure:
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
    assert (code, spent < smoke_run.READY_SECONDS) == (1, True), text
    assert "S1: сервер вышел с кодом 1 до готовности" in lines, text
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


def test_a372_s2_is_the_ci_test_step_without_coverage() -> None:
    """A372: S2 — команда шага «Тесты с покрытием» из `backend/` без `--cov…`."""
    jobs = yaml.safe_load(QUALITY.read_text(encoding="utf-8"))["jobs"]
    step = next(s for s in jobs["tests"]["steps"] if s.get("name") == "Тесты с покрытием")
    words = str(step["run"]).splitlines()[0].split()
    found = (step["working-directory"], [w for w in words if not w.startswith("--cov")])
    assert found == ("backend", list(smoke_run.S2_COMMAND))


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
        alive_before = not gone(pgid)
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
        except smoke_run.Failed as failure:
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
