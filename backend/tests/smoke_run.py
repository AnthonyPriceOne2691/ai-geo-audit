"""Сквозные проверки smoke (§6.1, спека smoke, A370–A379): S3 — миграции с нуля, S1 — сервер
настоящей точкой входа, S2 — набор backend.

Вход — `delivery/evals/smoke/run.sh`: интерпретатор окружения копии, `PYTHONPATH` — `backend/src`
корня. Тесты зовут функции напрямую — со своим `.smoke/` в `tmp_path` и своим именем базы.

Ход прогона: замок копии → остатки прошлого прогона → путь пакета и Postgres → сценарии подряд →
уборка: сервер и база smoke. В выводе — строка на сценарий; вывод самого сценария — в файл в
`.smoke/`, при отказе — строка причины и хвост: строка причины попадает в первые 15 строк — окно
отказа `merge_guard`. Непредусмотренный сбой — тоже строка причины, «сбой <Тип>: <текст>»; сбой
уборки — своей строкой после итога. SIGINT и SIGTERM — обвязкой оракула (`oracle_guard.guarded`,
A74): уборка ровно одна и без сигналов, код 128 + номер первого сигнала.
"""

from __future__ import annotations

import envguard  # isort: skip

envguard.clean()  # до первого импорта пакета: конфиг читает окружение при импорте (A31)

import asyncio
import contextlib
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import time
import traceback
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import asyncpg
import dbtools
import httpx
import live_server
from alembic.config import Config
from alembic.script import ScriptDirectory

import ai_geo_audit
from ai_geo_audit.storage import models

# Обвязка сигналов оракула (A74) лежит в `scripts/`, вне пакета; путь — в конец `sys.path`: модули
# `scripts/` не заслоняют ни один другой.
sys.path.append(str(envguard.ROOT / "scripts"))
import oracle_guard

HERE = Path(__file__).resolve()
ROOT = envguard.ROOT
BACKEND = ROOT / "backend"
README = ROOT / "delivery" / "evals" / "smoke" / "README.md"
STATE = ".smoke"
SERVER_FILE = "server.json"
#: A55: готовность сервера и конец сигналом `SIGTERM`.
READY_SECONDS = 15.0
STOP_SECONDS = 25.0
TAIL_LINES = 30
BUSY = "smoke уже идёт в этой копии"
FOREIGN = "судится чужой пакет"
#: Команда шага «Тесты с покрытием» без `--cov…` (A372); `uv` — только с `--no-sync` (A377).
S2_COMMAND = ("uv", "run", "--no-sync", "pytest")
#: Строка таблицы README: id и предел в секундах — последний столбец (A375).
ROW = re.compile(r"(?m)^\|\s*(S\d+)\s*\|.*\|\s*(\d+)\s*с\s*\|\s*$")
TEST_NAME = re.compile(r"audit_test_[0-9a-f]{10}")


class ScenarioError(Exception):
    """Сценарий красный; текст — причина одной строкой."""


class TimedOutError(ScenarioError):
    """Сценарий дольше своего предела; строку «предел N с» пишет прогон."""


class BusyError(Exception):
    """Замок копии держит другой прогон (A378)."""


def say_line(text: str) -> None:
    print(text, flush=True)


@dataclass
class Run:
    """Прогон: корень копии, папка состояния, имя базы smoke, каталог пакета подпроцессов."""

    root: Path
    state: Path
    db: str
    src: Path
    say: Callable[[str], None] = say_line
    ready_seconds: float = READY_SECONDS
    stop_seconds: float = STOP_SECONDS
    server: subprocess.Popen[bytes] | None = field(default=None, repr=False)
    #: Postgres ответил до сценариев: база smoke — на уборке; иначе прогон её не трогал.
    reached: bool = False

    @classmethod
    def of(cls, root: Path) -> Run:
        """Прогон копии: `.smoke/` в корне, база smoke — от корня, пакет — `backend/src`."""
        root = root.resolve()
        return cls(root, root / STATE, dbtools.named(f"{root}:smoke"), root / "backend" / "src")

    def log(self, scenario: str) -> Path:
        return self.state / f"{scenario}.log"


@dataclass(frozen=True)
class Scenario:
    id: str
    limit: float
    run: Callable[[Run, float], None]


# --- процессы ------------------------------------------------------------------------------


#: «Группы нет»: ESRCH — снята; EPERM — на macOS так отвечает группа, где остался один зомби до
#: снятия init (проба 2026-11-24). Сигнал идёт только своей группе — лидер сверен с файлом.
GONE = (ProcessLookupError, PermissionError)


def kill_group(pgid: int, sig: int = signal.SIGKILL) -> None:
    """Сигнал группе процессов; группы уже нет — не ошибка."""
    with contextlib.suppress(*GONE):
        os.killpg(pgid, sig)


def wait_gone(pgid: int, seconds: float) -> bool:
    """Группы нет — не позже `seconds`; лидер — не наш ребёнок, поэтому опрос, а не `wait`."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except GONE:
            return True
        time.sleep(0.05)
    return False


def identity(pid: int) -> list[str] | None:
    """Время старта и команда процесса по `ps` в языке C: запись лидера и сверка с ней — одна
    строка при любых `LC_ALL` и `LANG` прогонов (проба второй проверяющей 2026-11-24: C, ru_RU и
    en_GB давали три разные строки времени); процесса нет — None."""
    found: list[str] = []
    for column in ("lstart=", "command="):
        done = subprocess.run(
            ["ps", "-ww", "-o", column, "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
        line = done.stdout.strip()
        if done.returncode != 0 or not line:
            return None
        found.append(line)
    return found


def spawn(command: Sequence[str], log: Path, env: Mapping[str, str]) -> subprocess.Popen[bytes]:
    """Команда из `backend/` своей группой процессов; вывод — в конец файла `log`."""
    with log.open("ab") as out:
        return subprocess.Popen(
            list(command),
            cwd=BACKEND,
            env=dict(env),
            stdout=out,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


def run_limited(command: Sequence[str], log: Path, deadline: float, env: Mapping[str, str]) -> int:
    """Код выхода команды; дольше срока — `TimedOutError`, группа снята при любом исходе."""
    process = spawn(command, log, env)
    try:
        return process.wait(timeout=max(deadline - time.monotonic(), 0.0))
    except subprocess.TimeoutExpired:
        raise TimedOutError("предел сценария") from None
    finally:
        if process.returncode is None:
            kill_group(process.pid)
            process.wait()


# --- сервер S1 и его группа ----------------------------------------------------------------


def launch(run: Run, command: Sequence[str], env: Mapping[str, str]) -> subprocess.Popen[bytes]:
    """Сервер своей группой процессов. Pid группы, время старта и команда лидера лежат в
    `.smoke/` до остановки: `kill -9` всего `run.sh` сервер переживает (A379)."""
    server = spawn(command, run.log("S1"), env)
    run.server = server
    record = {"pgid": server.pid, "leader": identity(server.pid)}
    temporary = run.state / f"{SERVER_FILE}.tmp"
    temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    temporary.replace(run.state / SERVER_FILE)
    return server


def forget(run: Run) -> None:
    run.server = None
    (run.state / SERVER_FILE).unlink(missing_ok=True)


def wait_ready(run: Run, server: subprocess.Popen[bytes], port: int, ready_by: float) -> None:
    """`GET /api/health` 200 до срока `ready_by` по `monotonic` — общего с пробой (L83). Сервер
    вышел раньше — отказ сразу, с кодом выхода, а не по сроку (A373)."""
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=1.0) as http:
        while time.monotonic() < ready_by:
            code = server.poll()
            if code is not None:
                raise ScenarioError(f"сервер вышел с кодом {code} до готовности")
            with contextlib.suppress(httpx.TransportError):
                if http.get("/api/health").status_code == 200:
                    return
            time.sleep(0.1)
    raise ScenarioError(f"сервер не готов за {run.ready_seconds:g} с")


def stop_server(run: Run, server: subprocess.Popen[bytes]) -> None:
    """`SIGTERM` группе: зелёное — конец сигналом `SIGTERM` не позже срока (A55); выход кодом или
    `killpg` по сроку — красное с причиной (A371). Файла группы после — нет."""
    kill_group(server.pid, signal.SIGTERM)
    try:
        code = server.wait(timeout=run.stop_seconds)
    except subprocess.TimeoutExpired:
        kill_group(server.pid)
        server.wait()
        cause = f"сервер не вышел за {run.stop_seconds:g} с после SIGTERM — снят killpg"
        raise ScenarioError(cause) from None
    finally:
        forget(run)
    if code != -signal.SIGTERM:
        raise ScenarioError(f"сервер вышел кодом {code}, а не сигналом SIGTERM")


def halt(run: Run) -> None:
    """Сервер, если жив: `SIGTERM` группе, через 5 с — `SIGKILL`; файла группы после — нет."""
    server = run.server
    if server is not None and server.poll() is None:
        kill_group(server.pid, signal.SIGTERM)
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            kill_group(server.pid)
            server.wait()
    forget(run)


def leftover_group(state: Path) -> list[str]:
    """Группа сервера прошлого прогона по файлу в `.smoke/`: снимается, только если её лидер — тот
    же сервер (время старта и команда совпали); иначе pid повторился (L97) — не тронута. Файл
    снимается в обоих случаях (A379)."""
    path = state / SERVER_FILE
    if not path.is_file():
        return []
    record = json.loads(path.read_text(encoding="utf-8"))
    path.unlink()
    pgid = int(record["pgid"])
    leader = identity(pgid)
    if leader is None:
        return [f"группы {pgid} уже нет"]
    if leader != record.get("leader"):
        return [f"группа {pgid} — лидер не наш сервер (pid повторился), не тронута"]
    kill_group(pgid, signal.SIGTERM)
    if not wait_gone(pgid, STOP_SECONDS):
        kill_group(pgid)
        wait_gone(pgid, 5)
    return [f"группа {pgid} снята"]


@contextlib.contextmanager
def copy_lock(state: Path) -> Iterator[Path]:
    """Замок одной копии без ожидания (`flock`): занят — `BusyError` сразу (A378). Держит процесс,
    а не файл: после `kill -9` замок снимает ядро; дескриптор подпроцессам не наследуется
    (PEP 446)."""
    state.mkdir(parents=True, exist_ok=True)
    path = state / "lock"
    with path.open("a", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise BusyError(f"{BUSY}: замок {path} держит другой прогон") from None
        yield path


# --- окружение и базы ----------------------------------------------------------------------


def package_problem(root: Path, package_file: Path) -> str | None:
    """Пакет `ai_geo_audit` — из `backend/src` этой копии. `resolve()` с обеих сторон: на macOS
    `/var` — ссылка на `/private/var`, а дерево `merge_guard` — в `mktemp -d` (A377)."""
    where = Path(package_file).resolve().parent
    if where.parent == (root / "backend" / "src").resolve():
        return None
    return f"{FOREIGN}: {where}"


async def postgres_problem() -> str | None:
    reason = await dbtools.unreachable()
    return f"окружение: Postgres не отвечает ({reason}) — {dbtools.START}" if reason else None


def url_of(db: str) -> str:
    """Адрес базы `db` на сервере тестов — как у базы рабочей копии (`dbtools.own_url`)."""
    with dbtools.active(db):
        return dbtools.own_url()


def require_test_name(db: str) -> None:
    if not TEST_NAME.fullmatch(db):
        raise dbtools.RefusedError(f"создание и снос — только audit_test_…; «{db}» не тронута")


async def recreate(db: str) -> None:
    """База `db` с нуля: прежняя с тем же именем снимается до создания."""
    require_test_name(db)
    conn = await asyncpg.connect(dbtools._raw(dbtools.server_url()), timeout=3)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{db}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{db}"')
    finally:
        await conn.close()


async def drop(db: str) -> bool:
    """Снести базу `db`, если она есть; True — была."""
    require_test_name(db)
    conn = await asyncpg.connect(dbtools._raw(dbtools.server_url()), timeout=3)
    try:
        existed = bool(await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db))
        if existed:
            await conn.execute(f'DROP DATABASE "{db}" WITH (FORCE)')
        return existed
    finally:
        await conn.close()


def head_revision() -> str:
    head = ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini"))).get_current_head()
    if head is None:
        raise ScenarioError("у цепочки ревизий нет head")
    return head


async def schema_problem(db: str) -> str | None:
    """Итог, а не код выхода (L28): ревизия базы — head цепочки, все таблицы
    `models.Base.metadata` есть в `public`."""
    conn = await asyncpg.connect(dbtools._raw(url_of(db)), timeout=3)
    try:
        version = None
        if await conn.fetchval("SELECT to_regclass('public.alembic_version')"):
            version = await conn.fetchval("SELECT version_num FROM alembic_version")
        rows = await conn.fetch("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
    finally:
        await conn.close()
    head = head_revision()
    if version != head:
        return f"ревизия базы не head: {version or 'нет'} вместо {head}"
    missing = sorted(set(models.Base.metadata.tables) - {row["tablename"] for row in rows})
    return f"нет таблиц схемы: {', '.join(missing)}" if missing else None


# --- сценарии ------------------------------------------------------------------------------


def s3(run: Run, deadline: float, command: Sequence[str] | None = None) -> None:
    """S3: пустая база smoke → `alembic upgrade head` из `backend/` той же командой, что сервис
    `migrate` стека (L19); каталог пакета — в `PYTHONPATH`; судится итог (A370, A374)."""
    asyncio.run(recreate(run.db))
    with dbtools.active(run.db):
        env = dbtools.alembic_env(extra={"PYTHONPATH": str(run.src)})
    alembic = command or (str(Path(sys.executable).with_name("alembic")), "upgrade", "head")
    code = run_limited(alembic, run.log("S3"), deadline, env)
    if code != 0:
        raise ScenarioError(f"alembic upgrade head — код {code}")
    problem = asyncio.run(schema_problem(run.db))
    if problem:
        raise ScenarioError(problem)


def s1(
    run: Run, _deadline: float, port: int | None = None, probe: Sequence[str] | None = None
) -> None:
    """S1: на базе S3 сервер настоящей точкой входа своей группой процессов (L55); окружение —
    значения настроек тестов, как A55. Готовность и проба `api.probe` с кодом 0 — одним сроком, не
    позже 15 с после старта (A371, L83); остановка — `SIGTERM`, не позже 25 с. Предел сценария,
    60 с, выше суммы сроков: своего таймера у S1 нет."""
    port = port or live_server.free_port()
    env = live_server.live_env(port, url_of(run.db), run.src)
    ready_by = time.monotonic() + run.ready_seconds
    server = launch(run, (sys.executable, "-m", "ai_geo_audit.api"), env)
    try:
        wait_ready(run, server, port, ready_by)
        health = probe or (sys.executable, "-m", "ai_geo_audit.api.probe")
        try:
            code = run_limited(health, run.log("S1"), ready_by, env)
        except TimedOutError:
            cause = f"проба здоровья не кончилась за {run.ready_seconds:g} с после старта"
            raise ScenarioError(cause) from None
        if code != 0:
            raise ScenarioError(f"проба здоровья — код {code}")
        stop_server(run, server)
    finally:
        halt(run)


def s2(run: Run, deadline: float, command: Sequence[str] = S2_COMMAND) -> None:
    """S2: набор из `backend/` командой шага «Тесты с покрытием» без `--cov…` (A372); каталог
    пакета — в `PYTHONPATH`: `pythonpath` pytest его не ставит. База набора своего корня
    снимается при любом исходе: тесты её не сносят, а у `merge_guard` корень всякий раз новый."""
    env = {**os.environ, "PYTHONPATH": str(run.src)}
    try:
        code = run_limited(command, run.log("S2"), deadline, env)
    finally:
        asyncio.run(drop(dbtools.own_name(run.root)))
    if code != 0:
        raise ScenarioError(f"набор — код {code}")


#: Порядок — порядок таблицы README: S1 идёт на базе S3. Пределы — с запасом (L67, L101).
SCENARIOS = (Scenario("S3", 120.0, s3), Scenario("S1", 60.0, s1), Scenario("S2", 600.0, s2))


# --- прогон --------------------------------------------------------------------------------


def table(text: str) -> list[tuple[str, float]]:
    """Строки таблицы README: id и предел в секундах (A375)."""
    return [(found.group(1), float(found.group(2))) for found in ROW.finditer(text)]


def summary(passed: Sequence[str], scenarios: Sequence[Scenario], skip: Mapping[str, str]) -> str:
    line = f"сценариев {len(passed)} из {len(scenarios)}"
    skipped = [f"{s.id}: {skip[s.id]}" for s in scenarios if s.id in skip]
    return f"{line}, пропущен {'; '.join(skipped)}" if skipped else line


def tail(run: Run, scenario: str) -> None:
    lines = run.log(scenario).read_text(encoding="utf-8", errors="replace").splitlines()
    for line in lines[-TAIL_LINES:]:
        run.say(f"    {line}")


def failure_line(failure: Exception) -> str:
    """Непредусмотренный сбой одной строкой: тип и текст исключения."""
    return f"сбой {type(failure).__name__}: {failure}"


def attempt(run: Run, scenario: Scenario) -> str | None:
    """Один сценарий под своим пределом; причина отказа или None. Непредусмотренный сбой (Postgres
    пропал посреди сценария) — «сбой <Тип>: <текст>», трассировка — в конец файла сценария:
    причина встаёт в окно 15 строк, а трассировка — в хвост за ней."""
    run.log(scenario.id).write_bytes(b"")
    began = time.monotonic()
    try:
        scenario.run(run, began + scenario.limit)
    except TimedOutError:
        return f"предел {scenario.limit:g} с"
    except ScenarioError as failure:
        return str(failure)
    except Exception as failure:
        with run.log(scenario.id).open("a", encoding="utf-8") as log:
            log.write(traceback.format_exc())
        return failure_line(failure)
    run.say(f"{scenario.id}: зелёный за {time.monotonic() - began:.0f} с")
    return None


def runner(run: Run, scenarios: Sequence[Scenario], skip: Mapping[str, str]) -> int:
    """Сценарии подряд: первый сбой — код 1, строка причины, хвост вывода и итог; ноль
    засчитанных — красное (L73)."""
    passed: list[str] = []
    for scenario in scenarios:
        if scenario.id in skip:
            run.say(f"{scenario.id}: пропущен — {skip[scenario.id]}")
            continue
        cause = attempt(run, scenario)
        if cause:
            run.say(f"{scenario.id}: {cause}")
            tail(run, scenario.id)
            run.say(summary(passed, scenarios, skip))
            return 1
        passed.append(scenario.id)
    run.say(summary(passed, scenarios, skip))
    return 0 if passed else 1


def report(run: Run, notes: Sequence[str]) -> None:
    if notes:
        run.say(f"от прошлого прогона: {', '.join(notes)}")


def prepared(
    run: Run, scenarios: Sequence[Scenario], skip: Mapping[str, str], package: Path
) -> int:
    """Тело прогона под замком: путь пакета первой строкой, остатки прошлого прогона, окружение,
    сценарии. Сбой до сценариев — строкой «до сценариев: сбой <Тип>: <текст>», код 1."""
    run.say(f"пакет: {Path(package).resolve().parent}")
    notes: list[str] = []
    try:
        notes += leftover_group(run.state)
        problem = package_problem(run.root, package) or asyncio.run(postgres_problem())
        run.reached = problem is None
        if run.reached and asyncio.run(drop(run.db)):
            notes.append("база снята")
    except Exception as failure:
        problem = f"до сценариев: {failure_line(failure)}"
    report(run, notes)
    if problem:
        run.say(problem)
        return 1
    return runner(run, scenarios, skip)


def cleanup(run: Run) -> bool:
    """Уборка прогона при любом исходе — сервер и база smoke, если Postgres ответил до сценариев
    (A370, A371); `guarded` зовёт её ровно один раз и без сигналов. Сбой уборки — своей строкой
    после итога: причину сценария он не заменяет. True — убрано."""
    try:
        halt(run)
        if run.reached:
            asyncio.run(drop(run.db))
    except Exception as failure:
        run.say(f"уборка: {failure_line(failure)}")
        return False
    return True


def smoke(run: Run, scenarios: Sequence[Scenario], skip: Mapping[str, str], package: Path) -> int:
    """Прогон копии под замком; занят — код 1 «smoke уже идёт в этой копии», `.smoke/` и базы не
    тронуты (A378). Тело — под SIGINT и SIGTERM обвязкой оракула (A74): уборка одна и без
    сигналов, код — 128 + номер первого сигнала, обработчики после — прежние. Сбой уборки —
    код 1."""
    try:
        with copy_lock(run.state):
            stopped, code, clean = oracle_guard.guarded(
                lambda: prepared(run, scenarios, skip, package), lambda: cleanup(run)
            )
    except BusyError as busy:
        run.say(str(busy))
        return 1
    if stopped is not None:
        swept = ": сервер и база smoke сняты" if clean else ""
        run.say(f"прерван сигналом {stopped - 128}{swept}")
        return stopped
    return (code or 0) if clean else 1


def parse_skip(args: Sequence[str], scenarios: Sequence[Scenario]) -> dict[str, str]:
    """`--skip ID=причина`: пропуск — только по имени из таблицы и с причиной (A376)."""
    ids = {s.id for s in scenarios}
    skip: dict[str, str] = {}
    rest = list(args)
    while rest:
        flag = rest.pop(0)
        scenario, _, why = (rest.pop(0) if rest else "").partition("=")
        if flag != "--skip" or scenario not in ids or not why.strip():
            raise SystemExit("run.sh [--skip ID=причина]…: ID — из таблицы README, причина словами")
        skip[scenario] = why.strip()
    return skip


def main(argv: Sequence[str]) -> int:
    skip = parse_skip(argv, SCENARIOS)
    return smoke(Run.of(ROOT), SCENARIOS, skip, Path(ai_geo_audit.__file__))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
