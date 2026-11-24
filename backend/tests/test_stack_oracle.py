"""Чистые части оракула стека без Docker (A61, A62, A82): поиск метки по выгрузке образа, разбор
контекста сборки, сверка списков файлов, имя стека, порт web и подсеть `front`. Модуль грузится по
пути файла: оракул лежит вне пакета и пакет не импортирует.
"""

from __future__ import annotations

import importlib.util
import io
import ipaddress
import socket
import subprocess
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace

import dbtools
import pytest

sys.path.insert(0, str(dbtools.ROOT / "scripts"))
_spec = importlib.util.spec_from_file_location(
    "stack_oracle", dbtools.ROOT / "scripts" / "stack_oracle.py"
)
oracle = importlib.util.module_from_spec(_spec)
sys.modules["stack_oracle"] = oracle
_spec.loader.exec_module(oracle)
compose = importlib.import_module("stack_oracle_compose")

LABEL = "oracle-canary-0f0f0f0f"


def archive(files: dict[str, bytes]) -> io.BytesIO:
    """A61: выгрузка образа в миниатюре — tar с файлами и каталогом."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        directory = tarfile.TarInfo("usr")
        directory.type = tarfile.DIRTYPE
        tar.addfile(directory)
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    buffer.seek(0)
    return buffer


def test_a61_label_is_found_in_contents_and_in_names() -> None:
    """A61: метка в содержимом и в имени файла найдена; `.env*` названы; число просмотренных."""
    scan = oracle.scan_tar(
        archive(
            {
                "usr/a.txt": b"clean",
                "app/x.cfg": f"key={LABEL}".encode(),
                f"tmp/{LABEL}.txt": b"clean",
                "app/.env.local": b"clean",
            }
        ),
        LABEL,
    )
    assert scan.scanned == 4
    assert sorted(scan.hits) == ["app/x.cfg", f"tmp/{LABEL}.txt"]
    assert scan.envs == ["app/.env.local"]
    assert len(oracle.label_problems(scan)) == 2


def test_a61_clean_export_passes_and_an_empty_one_is_refused() -> None:
    """A61: без метки и `.env*` — чисто; ноль просмотренных файлов — отказ, а не «чисто»."""
    assert oracle.label_problems(oracle.scan_tar(archive({"usr/a.txt": b"clean"}), LABEL)) == []
    empty = oracle.label_problems(oracle.scan_tar(archive({}), LABEL))
    assert len(empty) == 1 and "0" in empty[0]


def test_a61_scan_reads_only_under_the_prefix() -> None:
    """A61: проба контекста читает только свой каталог в выгрузке."""
    scan = oracle.scan_tar(archive({"ctx/backend/uv.lock": b"x", "usr/a": b"y"}), LABEL, "ctx/")
    assert scan.names == {"backend/uv.lock"} and scan.scanned == 1


OPEN = {
    "backend/pyproject.toml",
    "backend/uv.lock",
    "backend/src/ai_geo_audit/__init__.py",
    "backend/alembic.ini",
    "backend/migrations/env.py",
}


@pytest.mark.parametrize(
    ("names", "expected"),
    [
        (OPEN, 0),
        (OPEN - {"backend/uv.lock"}, 1),
        (OPEN | {"backend/tests/test_x.py"}, 1),
        (OPEN | {"private/notes.md"}, 1),
        (OPEN | {"backend/src/ai_geo_audit/.env.local"}, 1),
    ],
    ids=["ровно пять", "нет пути", "лишний из backend", "личный каталог", "канарейка"],
)
def test_a61_context_has_exactly_the_five_open_paths(names: set[str], expected: int) -> None:
    """A61: в контексте ровно пять открытых путей; лишний путь и канарейка — нарушение."""
    assert len(oracle.context_problems(names)) == expected


def test_a62_file_lists_are_compared_both_ways() -> None:
    """A62: сверка списков называет и пропавшие в образе, и лишние в нём файлы."""
    copy = {"api/app.py", "_oracle_probe.txt"}
    assert oracle.compare("пакет", copy, copy) == []
    problems = oracle.compare("пакет", copy, {"api/app.py", "stray.py"})
    assert problems == [
        "пакет: нет в образе — _oracle_probe.txt",
        "пакет: лишние в образе — stray.py",
    ]


def test_a62_copy_files_skip_python_cache_and_env_canaries(tmp_path: Path) -> None:
    """A62: файлы копии — без кэша Python и без канареек `.env*`, с подложенным файлом данных."""
    for name in ("pkg/a.py", "pkg/__pycache__/a.cpython-312.pyc", "pkg/.env.prod", "pkg/data.txt"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("x", encoding="utf-8")
    assert oracle.files_under(tmp_path / "pkg") == {"a.py", "data.txt"}


def test_a61_stack_name_is_the_prefix_and_ten_signs_of_the_path_hash() -> None:
    """A61: имя стека — `ai-geo-audit-oracle-` и 10 знаков sha256 пути копии."""
    name = oracle.stack_name(Path("/work/copy-a"))
    assert name.startswith("ai-geo-audit-oracle-") and len(name) == len("ai-geo-audit-oracle-") + 10
    assert name != oracle.stack_name(Path("/work/copy-b"))
    assert name == oracle.stack_name(Path("/work/copy-a"))


def test_a65_a67_docker_times_and_the_zero_time() -> None:
    """A65, A67: время Docker с наносекундами читается; нулевое время — «не стартовал»."""
    assert compose.parse_time("0001-01-01T00:00:00Z") is None
    early = compose.parse_time("2026-11-20T09:30:01.123456789Z")
    late = compose.parse_time("2026-11-20T09:30:02Z")
    assert early is not None and late is not None
    assert early < late and early.microsecond == 123456


def test_a63_process_users_are_read_under_the_top_header() -> None:
    """A63: пользователи процессов — столбец UID под заголовком `docker compose top`."""
    head = "stack-api-1\nUID    PID   PPID  C  STIME  TTY  TIME  CMD\n"
    rows = (
        "10001  101   1     0  09:30  ?    0:00  tini\n10001  102   101   0  09:30  ?    0:01  py\n"
    )
    assert compose.top_uids(head + rows) == {"10001"}
    assert compose.top_uids(head + rows.replace("10001  102", "root   102")) == {"10001", "root"}


def test_a63_process_users_in_the_compose_5_top_layout() -> None:
    """A63: у Compose 5.x (замер 5.7.0) первые столбцы `top` — SERVICE и #, UID — третий."""
    text = (
        "SERVICE  #   UID    PID    PPID   C   STIME  TTY  TIME      CMD\n"
        "api      1   10001  81294  81270  0   09:38  ?    00:00:00  /sbin/docker-init -- python\n"
        "api      1   10001  81317  81294  35  09:38  ?    00:00:00  python -m ai_geo_audit.api\n"
    )
    assert compose.top_uids(text) == {"10001"}


class LeftBehind(oracle.Docker):
    """Подставной исполнитель: уборка прошла, а по меткам compose остались контейнер и том."""

    def result(self, *args: str, limit: str = "run", **kw: object) -> subprocess.CompletedProcess:
        left = {("container", "ls"): b"c0ffee000001\n", ("volume", "ls"): b"oracle_db-data\n"}
        return subprocess.CompletedProcess(["docker", *args], 0, left.get(args[:2], b""), b"")


def test_a65_left_behind_container_or_volume_makes_the_run_red(tmp_path: Path) -> None:
    """A65: своё, оставшееся после уборки, — красное и код не ноль, даже если шаги зелёные."""
    run = oracle.Run(LeftBehind(), tmp_path / "copy", "ai-geo-audit-oracle-test", LABEL, {})
    left = run.cleanup()
    assert left == ["container c0ffee000001", "volume oracle_db-data"]
    code, line = oracle.verdict(0, left)
    assert code == 1 and "КРАСНОЕ" in line and "oracle_db-data" in line
    assert oracle.verdict(0, []) == (0, f"итог: шагов {len(oracle.STEPS)}, красных 0")


#: Примеры шагов оракула (A76, первая строка): у каждого шага — номер своего примера; Ч1.5б,
#: срез 1 — A77 и A82, срез 2 — A78, A79 и A80, срез 3 — A81, A83 и A84, срез 4 — A86 и A87.
STEP_EXAMPLES = {"A60", "A61", "A62", "A63", "A64", "A65", "A66", "A67", "A69", "A73"}
STEP_EXAMPLES |= {"A77", "A78", "A79", "A80", "A81", "A82", "A83", "A84", "A86", "A87"}


def steps_problems(steps: list[tuple[str, str, object, bool]]) -> list[str]:
    """Расхождения таблицы шагов с примерами: шаг без номера, номер не из набора, пример без
    шага."""
    problems = [f"шаг «{title}» без номера примера" for numbers, title, *_ in steps if not numbers]
    seen = {number for numbers, *_ in steps for number in numbers.split()}
    problems += [f"номер {number} — не пример оракула" for number in sorted(seen - STEP_EXAMPLES)]
    return problems + [f"пример {number} без шага" for number in sorted(STEP_EXAMPLES - seen)]


def test_a76_steps_table_has_exactly_examples_a60_to_a67_a69_a73_a77_to_a84_a86_a87() -> None:
    """A76, первая строка: таблица шагов оракула — у каждого шага номер своего примера; набор
    номеров — ровно A60, A61, A62, A63, A64, A65, A66, A67, A69 и A73, с Ч1.5б — A77, A78, A79,
    A80, A81, A82, A83, A84, A86 и A87."""
    assert steps_problems(oracle.STEPS) == []


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ("номер стёрт", "шаг «сборка из чистой копии» без номера примера"),
        ("шаг A73 убран", "пример A73 без шага"),
        ("номер A68", "номер A68 — не пример оракула"),
    ],
)
def test_a76_step_without_a_number_or_an_example_without_a_step_is_red(
    change: str, problem: str
) -> None:
    """A76, первая строка, копии: шаг без номера, пример без шага, номер не из набора — красное с
    названием шага или номера."""
    steps = list(oracle.STEPS)
    if change == "номер стёрт":
        steps[0] = ("", *steps[0][1:])
    elif change == "шаг A73 убран":
        steps = [step for step in steps if "A73" not in step[0].split()]
    else:
        steps.append(("A68", "лишний шаг", steps[0][2], False))
    assert problem in steps_problems(steps)


#: Пулы докера по умолчанию для сетей без своей подсети: 172.17–172.31 и 192.168.
POOLS = ("172.17.0.0/16", "172.18.0.0/15", "172.20.0.0/14", "172.24.0.0/13", "192.168.0.0/16")


def free(port: int, subnet: str) -> bool:
    return False


def test_a82_web_port_and_front_subnet_come_from_the_stack_name() -> None:
    """A82, третья строка: у двух имён стека — разные порт web и подсеть `front`, оба из ряда от
    имени; занятый кандидат — следующий; пять занятых — отказ словами, шестой не пробуется.
    Подсети — частные и вне пулов докера: чужая сеть без своей подсети их не займёт."""
    names = ("ai-geo-audit-oracle-0123456789", "ai-geo-audit-oracle-9876543210")
    (port_a, net_a), (port_b, net_b) = (compose.web_slot(name, free) for name in names)
    assert port_a != port_b and net_a != net_b
    assert compose.web_slot(names[0], free) == (port_a, net_a)
    tried: list[tuple[int, str]] = []
    third = compose.web_slot(names[0], lambda *slot: tried.append(slot) or len(tried) < 3)
    assert third == tried[2] and tried[0] == (port_a, net_a) and len(set(tried)) == 3
    tried.clear()
    with pytest.raises(compose.StepError, match="заняты все 5 кандидатов"):
        compose.web_slot(names[0], lambda *slot: tried.append(slot) or True)
    assert len(set(tried)) == len(tried) == 5
    pools = [ipaddress.ip_network(pool) for pool in POOLS]
    for port, net in tried:
        subnet = ipaddress.ip_network(net)
        assert port > 1024 and subnet.is_private and not any(map(subnet.overlaps, pools)), net


def test_a82_candidate_is_taken_by_a_bound_port_or_an_overlapping_docker_net() -> None:
    """A82, третья строка: кандидат занят, если его порт на петле держит другой процесс или его
    подсеть пересекает сеть докера на машине; иначе свободен."""
    busy = compose.busy_on(["172.16.0.0/24", "fd00::/64"])
    with socket.socket() as holder:
        holder.bind(("127.0.0.1", 0))
        port = holder.getsockname()[1]
        assert busy(port, "172.16.9.0/28")
    assert busy(port, "172.16.0.16/28")
    assert not busy(port, "172.16.9.0/28")


class Networks:
    """Подставной исполнитель для `slot`: сети докера на машине — по подсети на сеть; вызовы —
    в счёт."""

    def __init__(self, subnets: list[str]) -> None:
        self.subnets = subnets
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *args: str, limit: str) -> str:
        self.calls.append(args)
        if args[:2] == ("network", "ls"):
            return "".join(f"net{index}\n" for index in range(len(self.subnets)))
        return "".join(f"{subnet} \n" for subnet in self.subnets)


@pytest.mark.parametrize("taken", [False, True])
def test_a82_slot_reads_docker_nets_once_and_skips_a_candidate_in_one(taken: bool) -> None:
    """A82, третья строка: порт и подсеть прогона выбираются один раз; без сетей докера их подсети
    не читаются; подсеть первого кандидата у сети докера — берётся следующий (L88)."""
    name = "ai-geo-audit-oracle-0123456789"
    first = compose.web_slot(name, free)
    docker = Networks([first[1]] if taken else [])
    run = SimpleNamespace(name=name, facts={}, docker=docker)
    got = compose.slot(run)
    assert compose.slot(run) == got
    expected = [("network", "ls"), ("network", "inspect")][: 1 + taken]
    assert [call[:2] for call in docker.calls] == expected
    assert got == compose.web_slot(name, compose.busy_on(docker.subnets))
    assert not taken or (docker.calls[1][-1] == "net0" and got[1] != first[1])


@pytest.mark.parametrize("subnet", ["172.16.0.0/28", "172.16.15.240/28"])
def test_a83_web_address_is_the_last_host_of_the_front_subnet_of_the_run(subnet: str) -> None:
    """A83, первая строка: адрес web — последний адрес узла подсети front прогона, не шлюз и не
    широковещательный; он же — `WEB_ADDRESS` окружения compose, то есть доверенный прокси api."""
    net = ipaddress.ip_network(subnet)
    address = compose.web_address(subnet)
    assert ipaddress.ip_address(address) == net.broadcast_address - 1
    run = SimpleNamespace(
        name="ai-geo-audit-oracle-0123456789",
        facts={"slot": (18100, subnet)},
        image="backend:oracle",
        password="p",
        jwt="j",
        copy=Path("/work/copy"),
    )
    env = compose.stack_env(run)
    assert (env["FRONT_SUBNET"], env["WEB_ADDRESS"]) == (subnet, address)


class Clean(oracle.Docker):
    """Подставной исполнитель: Docker отвечает пусто — уборка прошла, своего не осталось."""

    def result(self, *args: str, limit: str = "run", **kw: object) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(["docker", *args], 0, b"", b"")


def steps_log(
    *plan: tuple[str, bool, bool],
) -> tuple[list[tuple[str, str, object, bool]], list[str]]:
    """Шаги из плана (имя, красный ли, нужный ли) и журнал: какие шаги дошли до исполнения."""
    ran: list[str] = []

    def step(name: str, red: bool) -> object:
        def body(run: object) -> str:
            ran.append(name)
            if red:
                raise oracle.StepError(f"порча шага {name}")
            return "сделано"

        return body

    return [
        (f"A{n}", name, step(name, red), needed) for n, (name, red, needed) in enumerate(plan)
    ], ran


def test_a76_red_step_is_counted_named_and_an_unneeded_one_goes_on(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A76: красный шаг — в счёте и в журнале «КРАСНОЕ» с причиной; ненужный красный шаг следующие
    не останавливает."""
    steps, ran = steps_log(("первый", True, False), ("второй", False, False))
    run = oracle.Run(Clean(), tmp_path / "copy", "ai-geo-audit-oracle-test", LABEL, {})
    assert oracle.run_steps(run, steps) == 1
    assert ran == ["первый", "второй"]
    out = capsys.readouterr().out
    assert "A0 первый — КРАСНОЕ: порча шага первый" in out
    assert "A1 второй — ок: сделано" in out


def test_a76_red_needed_step_stops_the_rest(tmp_path: Path) -> None:
    """A76: нужный шаг красный — следующие не идут: судить их не на чем."""
    steps, ran = steps_log(("нужный", True, True), ("следующий", False, False))
    run = oracle.Run(Clean(), tmp_path / "copy", "ai-geo-audit-oracle-test", LABEL, {})
    assert oracle.run_steps(run, steps) == 1
    assert ran == ["нужный"]


@pytest.mark.parametrize(("red", "code"), [(True, 1), (False, 0)])
def test_a76_finish_result_is_the_steps_result(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], red: bool, code: int
) -> None:
    """A76: итог джобы — итог оракула: красный шаг — код 1 и «КРАСНОЕ» в журнале, без красных —
    код 0; уборка своего не оставила."""
    steps, _ = steps_log(("шаг", red, False))
    run = oracle.Run(Clean(), tmp_path / "copy", "ai-geo-audit-oracle-test", LABEL, {})
    assert oracle.finish(run, steps) == code
    out = capsys.readouterr().out
    assert f"красных {int(red)}" in out
    assert ("КРАСНОЕ" in out) is red


class DockerDown(oracle.Docker):
    """Подставной исполнитель: `docker version` не отвечает — нет команды или нет службы."""

    def __init__(self, missing: bool) -> None:
        super().__init__()
        self.missing = missing

    def result(self, *args: str, limit: str = "run", **kw: object) -> subprocess.CompletedProcess:
        if self.missing:
            raise FileNotFoundError("docker")
        return subprocess.CompletedProcess(["docker", *args], 1, b"", b"Cannot connect to daemon")


@pytest.mark.parametrize("missing", [True, False], ids=["no-binary", "no-daemon"])
def test_a76_docker_down_is_code_2_not_a_green_run(
    missing: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    """A76: Docker не отвечает — код 2 и подсказка, а не зелёный прогон без единого шага."""
    assert oracle.run_oracle("ai-geo-audit-oracle-test", DockerDown(missing)) == 2
    assert " ".join(oracle.UP_HINT.split()) in " ".join(capsys.readouterr().out.split())


#: Строка журнала web: проверка здоровья смотрит не туда, nginx ответил 404.
WEB_LOG = 'web-1  | 127.0.0.1 - - "GET /api/health HTTP/1.1" 404 153 "-" "Wget"\n'


class MigrateFails(oracle.Docker):
    """Подставной исполнитель: `up --wait` — код 1, migrate вышел 255; журнал migrate — строки с
    паролем и секретом стека, ошибка alembic и две строки после неё; журнал web — `WEB_LOG`."""

    secrets: tuple[str, str] = ("", "")
    logs_calls: tuple[tuple[str, ...], ...] = ()

    def result(self, *args: str, limit: str = "run", **kw: object) -> subprocess.CompletedProcess:
        if "up" in args:
            stderr = b'service "migrate" didn\'t complete successfully: exit 255\n'
            return subprocess.CompletedProcess(["docker", *args], 1, b"", stderr)
        if "logs" in args:
            self.logs_calls += (args,)
            password, jwt = self.secrets
            log = (
                f"migrate-1  | url={password}\nmigrate-1  | jwt={jwt}\n"
                "migrate-1  | FAILED: Path doesn't exist: 'x'.\n"
                "migrate-1  | exit code 255\nmigrate-1  | done\n"
            )
            log = log if args[-1] == "migrate" else WEB_LOG
            return subprocess.CompletedProcess(["docker", *args], 0, log.encode(), b"")
        return subprocess.CompletedProcess(["docker", *args], 0, b"", b"")


def test_a76_failed_up_names_the_alembic_reason_without_the_password(tmp_path: Path) -> None:
    """A76, третья строка: подъём упал — в причине шага хвосты журналов migrate и web
    (`--tail 20`, шесть последних строк): причина от alembic («FAILED: …») и ответ nginx на
    проверку здоровья web (L89); пароля и секрета стека в причине нет."""
    docker = MigrateFails()
    run = oracle.Run(docker, tmp_path / "copy", "ai-geo-audit-oracle-test", LABEL, {})
    docker.secrets = (run.password, run.jwt)
    with pytest.raises(oracle.StepError) as failure:
        compose.step_up(run)
    reason = str(failure.value)
    assert "exit 255" in reason and "FAILED: Path doesn't exist" in reason
    assert '"GET /api/health HTTP/1.1" 404' in reason
    assert run.password not in reason and run.jwt not in reason
    tails = [call[-4:] for call in docker.logs_calls]
    assert tails == [("--no-color", "--tail", "20", service) for service in ("migrate", "web")]


class RulesAnswer(oracle.Docker):
    """Подставной исполнитель шага правил (A73): `config --services` — пять сервисов, `config
    --format json` — пустой стек, правила в образе — заданные код и вывод."""

    answer: tuple[int, str] = (0, "")

    def result(self, *args: str, limit: str = "run", **kw: object) -> subprocess.CompletedProcess:
        if "--services" in args:
            names = b"db\nredis\nmigrate\napi\nweb\n"
            return subprocess.CompletedProcess(["docker", *args], 0, names, b"")
        if "config" in args:
            return subprocess.CompletedProcess(["docker", *args], 0, b"{}", b"")
        code, text = self.answer
        return subprocess.CompletedProcess(["docker", *args], code, text.encode(), b"")


@pytest.mark.parametrize(
    ("code", "text", "red"),
    [
        (0, "итог: сервисов 5, правил 24, нарушений 0\n", ""),
        (
            0,
            "итог: сервисов 5, правил 23, нарушений 0\n",
            "правила — код 0: итог: сервисов 5, правил 23,",
        ),
        (0, "итог: сервисов 4, правил 24, нарушений 0\n", "правила — код 0: итог: сервисов 4,"),
        (
            1,
            "stop-grace: сервис api — срок\nитог: сервисов 5, правил 24, нарушений 1\n",
            "правила — код 1: stop-grace: сервис api — срок | итог",
        ),
    ],
    ids=["24", "23", "сервисов-4", "нарушение"],
)
def test_a73_a87_rules_step_wants_five_services_and_twenty_four_rules(
    tmp_path: Path, code: int, text: str, red: str
) -> None:
    """A73, A87: шаг правил на настоящем файле — зелёный только при коде 0 и итоге «сервисов 5,
    правил 24, нарушений 0»: шестнадцать кодов Ч1.5а, семь A87 и `stop-grace` A88; иначе
    красное с кодом и хвостом вывода."""
    docker = RulesAnswer()
    docker.answer = (code, text)
    run = oracle.Run(docker, tmp_path / "copy", "ai-geo-audit-oracle-test", LABEL, {})
    if not red:
        done = compose.step_rules_on_file(run)
        assert done == "итог: сервисов 5, правил 24, нарушений 0; config --services — 5"
        return
    with pytest.raises(oracle.StepError) as failure:
        compose.step_rules_on_file(run)
    assert str(failure.value).startswith(red)
