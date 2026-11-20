"""Обвязка оракула стека без Docker (A74, A75): замок одной копии, уборка своего имени, сигналы.

Модуль `scripts/oracle_guard.py` лежит вне пакета и грузится по пути файла. Исполнитель команд
Docker подставной: отвечает на запросы по метке проекта compose и пишет каждую команду.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import signal
import subprocess
import sys
from pathlib import Path

import dbtools
import pytest

_spec = importlib.util.spec_from_file_location(
    "oracle_guard", dbtools.ROOT / "scripts" / "oracle_guard.py"
)
assert _spec is not None
assert _spec.loader is not None
guard = importlib.util.module_from_spec(_spec)
sys.modules["oracle_guard"] = guard
_spec.loader.exec_module(guard)

OWN = "ai-geo-audit-oracle-0123456789"
DEV = "ai-geo-audit-dev"
LABEL = "label=com.docker.compose.project="


class Leftovers:
    """Подставной исполнитель: остатки двух стеков — своего имени и стека разработки. Как у
    Docker, `container ls` без `-a` даёт только работающие контейнеры; остановленный разовый
    `migrate` (после прогона он всегда Exited) виден только с `-a`."""

    def __init__(self) -> None:
        self.stacks = {
            OWN: {
                "container": ["c-own-api"],
                "stopped": ["c-own-migrate"],
                "volume": ["v-own-db"],
                "network": ["n-own"],
            },
            DEV: {
                "container": ["c-dev-1"],
                "stopped": ["c-dev-migrate"],
                "volume": ["v-dev-db"],
                "network": ["n-dev"],
            },
        }
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, *args: str, limit: str = "run", check: bool = True) -> str:
        self.commands.append(args)
        if len(args) > 1 and args[1] == "ls":
            marks = [a[len(LABEL) :] for a in args if a.startswith(LABEL)]
            stacks = [self.stacks[m] for m in marks if m in self.stacks] if marks else []
            if not marks:
                stacks = list(self.stacks.values())
            kinds = [args[0], *(["stopped"] if args[0] == "container" and "-a" in args else [])]
            return "".join(f"{i}\n" for s in stacks for k in kinds for i in s.get(k, []))
        return ""


def test_a75_stack_name_is_the_prefix_and_ten_signs_of_its_own_path() -> None:
    """A75, первая строка: имена двух копий разные, каждое — префикс и 10 знаков sha256 своего
    пути; `ai-geo-audit-dev` не выбирается ни для какого пути."""
    paths = [Path("/work/copy-a"), Path("/work/copy-b"), Path("/")]
    names = [guard.stack_name(p) for p in paths]
    assert len(set(names)) == len(names)
    for path, name in zip(paths, names, strict=True):
        digest = hashlib.sha256(str(path).encode()).hexdigest()[:10]
        assert name == f"ai-geo-audit-oracle-{digest}"
        assert re.fullmatch(r"ai-geo-audit-oracle-[0-9a-f]{10}", name)
    assert DEV not in names


def test_a74_start_cleanup_removes_only_its_own_name_and_counts() -> None:
    """A74, четвёртая строка: остатки двух стеков — убрано только своё (работающий и
    остановленный контейнеры, том, сеть), число — четыре; к `ai-geo-audit-dev` и его объектам
    команд нет."""
    docker = Leftovers()
    assert guard.start_cleanup(docker, OWN) == 4
    flat = [" ".join(c) for c in docker.commands]
    assert not [c for c in flat if DEV in c or "-dev" in c], flat
    removed = [c for c in docker.commands if "ls" not in c]
    assert removed == [
        ("rm", "-f", "-v", "c-own-api"),
        ("rm", "-f", "-v", "c-own-migrate"),
        ("volume", "rm", "-f", "v-own-db"),
        ("network", "rm", "n-own"),
    ]


def test_a74_start_cleanup_with_nothing_left_removes_nothing() -> None:
    """A74: остатков своего имени нет — ноль, ни одной команды удаления."""
    docker = Leftovers()
    docker.stacks.pop(OWN)
    assert guard.start_cleanup(docker, OWN) == 0
    assert [c for c in docker.commands if "ls" not in c] == []


def test_a75_second_lock_of_the_same_copy_is_refused_at_once(tmp_path: Path) -> None:
    """A75, третья строка: второй замок той же копии, пока держит первый, — отказ сразу со словами
    «оракул этой копии уже идёт»; замок первого не снят."""
    with guard.copy_lock(OWN, tmp_path):
        for _ in range(2):
            with (
                pytest.raises(guard.LockBusyError, match="оракул этой копии уже идёт"),
                guard.copy_lock(OWN, tmp_path),
            ):
                pass
    with guard.copy_lock(OWN, tmp_path):
        pass


def test_a75_lock_file_without_an_os_lock_does_not_block(tmp_path: Path) -> None:
    """A75, четвёртая строка: файл замка остался от прогона, убитого `kill -9`, блокировки ОС на
    нём нет — следующий прогон берёт замок; файл замок не удаляет."""
    stale = tmp_path / f"{OWN}.lock"
    stale.write_text("4242\n", encoding="utf-8")
    with guard.copy_lock(OWN, tmp_path) as path:
        assert path == stale
    assert stale.exists()


def test_a75_locks_of_two_copies_do_not_block_each_other(tmp_path: Path) -> None:
    """A75: замки двух копий — разные файлы по имени стека, друг друга не держат."""
    with (
        guard.copy_lock(OWN, tmp_path),
        guard.copy_lock("ai-geo-audit-oracle-9876543210", tmp_path),
    ):
        pass


def interrupted_by(
    first: signal.Signals, second: signal.Signals | None = None
) -> tuple[int | None, str | None, list[str], list[str], bool]:
    """Ход обвязки: тело получает `first`, уборка — `second`; что вызвано и с каким кодом."""
    calls: list[str] = []

    def body() -> str:
        calls.append("тело")
        os.kill(os.getpid(), first)
        calls.append("тело после сигнала")
        return "итог тела"

    def cleanup() -> list[str]:
        calls.append("уборка")
        if second is not None:
            os.kill(os.getpid(), second)
        calls.append("уборка до конца")
        return []

    before = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    code, outcome, left = guard.guarded(body, cleanup)
    after = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    return code, outcome, left, calls, before == after


@pytest.mark.parametrize(("sent", "code"), [(signal.SIGINT, 130), (signal.SIGTERM, 143)])
def test_a74_signal_stops_the_body_and_cleans_up_once(sent: signal.Signals, code: int) -> None:
    """A74, первая и вторая строки: SIGINT — код 130, SIGTERM — 143; тело прервано, уборка
    одна и до конца; обработчики сигналов после обвязки — прежние."""
    got, outcome, left, calls, restored = interrupted_by(sent)
    assert (got, outcome, left) == (code, None, [])
    assert calls == ["тело", "уборка", "уборка до конца"]
    assert restored


def test_a74_second_signal_during_cleanup_gives_no_second_cleanup() -> None:
    """A74, вторая строка: SIGINT, а за ним SIGTERM посреди уборки, как при отмене джобы на
    GitHub, — уборка одна и доходит до конца, код первого сигнала — 130."""
    got, _, _, calls, restored = interrupted_by(signal.SIGINT, signal.SIGTERM)
    assert got == 130
    assert calls.count("уборка") == 1
    assert calls[-1] == "уборка до конца"
    assert restored


def test_a74_body_without_a_signal_returns_its_outcome_and_cleans_up() -> None:
    """A74: без сигнала — итог тела, уборка одна, кода сигнала нет."""
    calls: list[str] = []

    def cleanup() -> list[str]:
        calls.append("уборка")
        return ["x"]

    code, outcome, left = guard.guarded(lambda: "итог", cleanup)
    assert (code, outcome, left, calls) == (None, "итог", ["x"], ["уборка"])


def test_a74_signal_during_cleanup_after_a_quiet_body_does_not_cut_the_cleanup() -> None:
    """A74: тело вернулось без сигнала, SIGTERM пришёл посреди уборки — так бывает, когда джобу
    отменяют ровно на уборке в конце прогона: уборка одна и доходит до конца, итог тела на месте,
    кода сигнала нет; обработчики после обвязки — прежние."""
    calls: list[str] = []

    def cleanup() -> list[str]:
        calls.append("уборка")
        os.kill(os.getpid(), signal.SIGTERM)
        calls.append("уборка до конца")
        return []

    before = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    assert guard.guarded(lambda: "итог", cleanup) == (None, "итог", [])
    assert calls == ["уборка", "уборка до конца"]
    assert {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)} == before


sys.path.insert(0, str(dbtools.ROOT / "scripts"))
_oracle_spec = importlib.util.spec_from_file_location(
    "stack_oracle_for_guard", dbtools.ROOT / "scripts" / "stack_oracle.py"
)
assert _oracle_spec is not None
assert _oracle_spec.loader is not None
oracle = importlib.util.module_from_spec(_oracle_spec)
sys.modules["stack_oracle_for_guard"] = oracle
_oracle_spec.loader.exec_module(oracle)
STACK_NAME = re.compile(r"ai-geo-audit-[\w-]+")


class SignalDuringUp(oracle.Docker):  # type: ignore[name-defined,misc]
    """Подставной исполнитель оракула: сигнал на `up --wait`, второй — на `down`, если задан."""

    def __init__(self, first: signal.Signals, second: signal.Signals | None = None) -> None:
        super().__init__()
        self.first, self.second = first, second
        self.commands: list[tuple[str, ...]] = []

    def result(
        self, *args: str, limit: str = "run", **kw: object
    ) -> subprocess.CompletedProcess[bytes]:
        self.commands.append(args)
        if "up" in args and "--wait" in args:
            os.kill(os.getpid(), self.first)
        if "down" in args and self.second is not None:
            os.kill(os.getpid(), self.second)
        return subprocess.CompletedProcess(["docker", *args], 0, b"", b"")


#: Свои объекты прогона к `up --wait` — что записали шаги до подъёма: сборка (контейнер
#: `-inspect`, образ `-backend:oracle`), проба контекста (образ `-context:oracle`), две выгрузки
#: под одним именем `-export`, правила A73 (`-rules`).
OWN_CONTAINERS = ("inspect", "export", "export", "rules")
OWN_IMAGES = ("backend:oracle", "context:oracle")


def interrupted_run(
    tmp_path: Path, docker: SignalDuringUp
) -> tuple[int, list[tuple[str, ...]], str]:
    """Шаг подъёма стека под обвязкой оракула: код выхода, команды после `up --wait`, имя."""
    copy = tmp_path / "copy"
    copy.mkdir()
    (copy / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    name = guard.stack_name(copy)
    docker.containers.extend(f"{name}-{kind}" for kind in OWN_CONTAINERS)
    docker.images.extend(f"{name}-{kind}" for kind in OWN_IMAGES)
    run = oracle.Run(docker, copy, name, "oracle-canary-0", {})
    up = [step for step in oracle.STEPS if step[2].__name__ == "step_up"]
    code = oracle.finish(run, up)
    after = docker.commands[[("up" in c) for c in docker.commands].index(True) + 1 :]
    return code, after, name


def one_cleanup_pass(docker: SignalDuringUp, after: list[tuple[str, ...]], name: str) -> None:
    """A74: один проход уборки — одна `docker compose -p <имя прогона> down -v --remove-orphans`
    и удаление своих контейнеров и образов по точному имени, каждого — один раз; других
    удалений нет, других имён стека в командах нет."""
    downs = [c for c in after if "down" in c]
    assert len(downs) == 1, after
    down = downs[0]
    assert down[down.index("-p") + 1] == name, down
    assert {"-v", "--remove-orphans"} <= set(down)
    removals = [c for c in after if "down" in c or "rm" in c]
    own = [("rm", "-f", "-v", f"{name}-{kind}") for kind in dict.fromkeys(OWN_CONTAINERS)]
    own += [("image", "rm", "-f", f"{name}-{kind}") for kind in OWN_IMAGES]
    assert sorted(removals) == sorted([down, *own]), removals
    names = {m for c in docker.commands for m in STACK_NAME.findall(" ".join(c))}
    assert names and all(n.startswith(name) for n in names), names


@pytest.mark.parametrize(("sent", "code"), [(signal.SIGINT, 130), (signal.SIGTERM, 143)])
def test_a74_signal_during_up_gives_exactly_one_down_of_its_own_name(
    tmp_path: Path, sent: signal.Signals, code: int
) -> None:
    """A74, первая и вторая строки: сигнал во время `up --wait` — выход 130 (SIGINT) или 143
    (SIGTERM); после сигнала один проход уборки: одна `down -v --remove-orphans` своего имени и
    удаление своих трёх контейнеров и двух образов по точному имени — шесть удалений, каждое
    один раз, других нет."""
    docker = SignalDuringUp(sent)
    got, after, name = interrupted_run(tmp_path, docker)
    assert got == code
    one_cleanup_pass(docker, after, name)


def test_a74_sigint_then_sigterm_during_cleanup_gives_no_second_down(tmp_path: Path) -> None:
    """A74, вторая строка: SIGINT во время `up --wait`, SIGTERM посреди уборки, как при отмене
    джобы на GitHub, — проход уборки один и тот же по составу, код первого сигнала — 130."""
    docker = SignalDuringUp(signal.SIGINT, signal.SIGTERM)
    got, after, name = interrupted_run(tmp_path, docker)
    assert got == 130
    one_cleanup_pass(docker, after, name)


class StoppedAfterDown(oracle.Docker):  # type: ignore[name-defined,misc]
    """Подставной исполнитель уборки: после `down` остался остановленный контейнер своего имени;
    как у Docker, `container ls` видит его только с `-a`."""

    def __init__(self, name: str) -> None:
        super().__init__()
        self.mark = LABEL + name

    def result(
        self, *args: str, limit: str = "run", **kw: object
    ) -> subprocess.CompletedProcess[bytes]:
        found = args[:2] == ("container", "ls") and self.mark in args and "-a" in args
        return subprocess.CompletedProcess(["docker", *args], 0, b"c-own-migrate\n" * found, b"")


def test_a74_cleanup_names_a_stopped_container_of_its_own_name_left_after_down(
    tmp_path: Path,
) -> None:
    """A74: после уборки остался остановленный контейнер своего имени — проба остатка смотрит с
    `-a`, контейнер назван, итог прогона красный «после уборки осталось»."""
    copy = tmp_path / "copy"
    copy.mkdir()
    (copy / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    name = guard.stack_name(copy)
    left = oracle.Run(StoppedAfterDown(name), copy, name, "oracle-canary-0", {}).cleanup()
    assert left == ["container c-own-migrate"]
    code, line = oracle.verdict(0, left)
    assert code == 1
    assert "после уборки осталось container c-own-migrate" in line


class RunLeftovers(oracle.Docker):  # type: ignore[name-defined,misc]
    """Подставной исполнитель прогона: Docker отвечает версией, остатки стеков — как у
    `Leftovers`; все команды — в его списке."""

    def __init__(self) -> None:
        super().__init__()
        self.left = Leftovers()

    def result(
        self, *args: str, limit: str = "run", **kw: object
    ) -> subprocess.CompletedProcess[bytes]:
        out = "29.9.3\n" if args[:1] == ("version",) else self.left(*args)
        return subprocess.CompletedProcess(["docker", *args], 0, out.encode(), b"")


def test_a74_run_starts_with_cleanup_of_its_own_name_before_the_copy(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A74, четвёртая строка, и A75, четвёртая, на уровне прогона: остатки своего имени и
    `ai-geo-audit-dev` — прогон убирает своё до копии и шагов и печатает, сколько убрал; к стеку
    разработки команд нет. Копия здесь падает сразу — после уборки команд к Docker нет."""
    docker = RunLeftovers()

    def no_copy(target: Path) -> None:
        docker.left.commands.append(("копия",))
        raise OSError(f"копия {target.name} в тесте не нужна")

    monkeypatch.setattr(oracle, "copy_head", no_copy)
    assert oracle.run_oracle(OWN, docker) == 2
    assert "уборка на старте: своего имени убрано 4" in capsys.readouterr().out
    commands = docker.left.commands
    at = commands.index(("копия",))
    assert len([c for c in commands[:at] if "rm" in c]) == 4, commands
    assert commands[at + 1 :] == []
    assert not [c for c in commands if DEV in " ".join(c) or "-dev" in " ".join(c)], commands


def test_a75_second_oracle_of_the_same_copy_is_refused_before_docker(tmp_path: Path) -> None:
    """A75, третья строка, на уровне оракула: замок копии держит первый прогон — второй
    `stack_oracle.py` отказывает сразу, код ≠ 0, «оракул этой копии уже идёт», до Docker."""
    script = dbtools.ROOT / "scripts" / "stack_oracle.py"
    env = {**os.environ, "TMPDIR": str(tmp_path), "PATH": str(tmp_path / "no-docker")}
    with guard.copy_lock(guard.stack_name(dbtools.ROOT), tmp_path):
        done = subprocess.run(
            [sys.executable, str(script)],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    assert done.returncode == 2, done.stdout + done.stderr
    assert "оракул этой копии уже идёт" in done.stdout
    assert "Docker не отвечает" not in done.stdout


def joined(text: str) -> str:
    """Вывод со склеенными строками продолжения: перенос заменил пробел переводом строки с
    отступом, склейка возвращает строку как была."""
    return text.replace("\n" + guard.INDENT, " ")


def test_output_line_is_wrapped_at_100_signs_on_spaces_with_an_indent() -> None:
    """Спека, Non-functional: строка вывода оракула и сканера — до 100 знаков. Длинная
    переносится по пробелам, продолжение — с отступом; склейка возвращает её слово в слово."""
    text = "2026-11-20T11:26:16Z A60 Python и инструменты — ок: " + " ".join(
        f"слово{i}" for i in range(60)
    )
    lines = guard.wrapped(text)
    assert len(lines) > 1
    assert all(len(line) <= guard.WIDTH for line in lines), [len(line) for line in lines]
    assert all(line.startswith(guard.INDENT) for line in lines[1:])
    assert not lines[0].startswith(" ")
    assert joined("\n".join(lines)) == text


def test_table_rows_and_short_lines_are_not_wrapped() -> None:
    """Строка таблицы длиннее 100 знаков — как есть (спека: «кроме строк таблиц»); короткая
    строка — как есть."""
    row = "| " + " | ".join(["CVE-2026-0001"] * 12) + " |"
    assert len(row) > guard.WIDTH
    assert guard.wrapped(row) == [row]
    assert guard.wrapped("короткая строка") == ["короткая строка"]


def test_say_starts_with_utc_time_and_keeps_lines_short(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`say`: время UTC с Z в начале, строки до 100 знаков."""
    guard.say("уборка: " + "своё " * 40)
    lines = capsys.readouterr().out.splitlines()
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", lines[0][:20])
    assert len(lines) > 1
    assert all(len(line) <= guard.WIDTH for line in lines)


def test_oracle_step_with_a_long_result_prints_lines_of_at_most_100_signs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Шаг оракула с длинным итогом (у A60 в живом журнале — 222 знака): строки вывода до
    100 знаков, итог шага после склейки — целиком."""
    detail = (
        "Python 3.12.16; requires-python и uv.lock — 3.12; "
        + ("uv, pytest, uvloop — ModuleNotFoundError; " * 5).strip()
    )
    steps = [("A60", "Python и инструменты", lambda _run: detail, False)]
    assert oracle.run_steps(None, steps) == 0
    out = capsys.readouterr().out
    assert all(len(line) <= guard.WIDTH for line in out.splitlines()), out
    assert f"A60 Python и инструменты — ок: {detail}" in joined(out)
