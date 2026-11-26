"""Шаг оракула «скрипт выката на живом стеке» (6а-2, T5; A186, A189) без Docker: снимок соседа —
ядром скрипта выката с подставным исполнителем и пробой, разбор конфига — подставным `docker` по
сервису и подмене; значение подмены — только в окружении команды (L84). У каждой ветки — свой тест
(L88). Модули `scripts/` лежат вне пакета."""

from __future__ import annotations

import importlib
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import dbtools
import deploy_records as rec
import pytest
from deploy_fakes import FakeRunner, tool

sys.path.insert(0, str(dbtools.ROOT / "scripts"))
oracle = importlib.import_module("stack_oracle")
compose_mod = importlib.import_module("stack_oracle_compose")
web = importlib.import_module("stack_oracle_web")
hard = importlib.import_module("stack_oracle_hardening")

NAME = "ai-geo-audit-oracle-0123456789"
PORT = 18123
SERVICES = ("api", "db", "migrate", "redis", "web")
#: Стек прогона: db, redis, api и web запущены, миграции — разовые, остановлены.
STACK = [rec.inspect_line(f"{NAME}-{s}-1", NAME) for s in SERVICES if s != "migrate"]
STACK.append(rec.inspect_line(f"{NAME}-migrate-1", NAME, status="exited", health=None))
NAMES = [f"{NAME}-{service}-1" for service in SERVICES]
#: Выдуманное значение прогона (`Run.invented`): в выводе отказа его быть не должно.
INVENTED = "invented-word-1234567890"
CHECK = ("python", "-m", "ai_geo_audit.ops.config_check")
GOOD = "Конфиг процесса «{}» годен, режим prod: проверено имён: 19\n"
WHY = "DB_POOL_SIZE — Input should be less than or equal to 50"
REFUSAL = f"Процесс «migrate» не стартует, негодные настройки:\n  {WHY}\n"
#: Значение подмены: число, которого нет в шуме compose — в имени разового контейнера 12 знаков hex.
VALUE = "4000000001"
#: Шум `compose run` в stderr — проба живого оракула 2026-11-26T13:16:40Z: строки создания
#: разового контейнера до вывода команды.
NOISE = " Container {0}-{1}-run-9dfe187d7bbf Creating \n"
NOISE += " Container {0}-{1}-run-9dfe187d7bbf Created\n"
LINE = (
    "api и migrate — годен, режим prod; отказ импорта у migrate — код 1, без Traceback и "
    "значения; снимок соседа: общих 5, перезапусков 0, коды прежние; пропало 0, новых 0"
)


class Stack:
    """Подставной `docker` прогона: разбор конфига отвечает по ключу — `api`, `migrate` или
    `refusal` (подмена `-e`): с кодом 0 — в stdout, иначе — в stderr, как команда, а ответ из
    трёх частей — код, stdout и stderr как есть; у `run` в stderr сначала шум compose; `ps` стека —
    имена по очереди; после отказа импорта машина переходит в состояние `after`."""

    def __init__(
        self,
        machine: FakeRunner,
        answers: dict[str, tuple[Any, ...]],
        after: list[str] | None,
        names: list[list[str]],
    ) -> None:
        self.machine, self.answers, self.after, self.names = machine, answers, after, names
        self.calls: list[tuple[tuple[str, ...], dict[str, str]]] = []

    def result(
        self, *args: str, env: dict[str, str], **_kw: Any
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls.append((args, env))
        key = "refusal" if "-e" in args else args[-1]
        code, text, *given = self.answers[key]
        if key == "refusal" and self.after is not None:
            self.machine.become(self.after)
        noise = NOISE.format(NAME, args[-1]) if "run" in args else ""
        out, err = (text, noise) if code == 0 else ("", noise + text)
        if given:
            out, err = text, given[0]
        return subprocess.CompletedProcess(["docker"], code, out.encode(), err.encode())

    def __call__(self, *args: str, **_kw: Any) -> str:
        label = f"label=com.docker.compose.project={NAME}"
        assert args == ("ps", "--all", "--filter", label, "--format", "{{.Names}}")
        return "".join(f"{name}\n" for name in self.names.pop(0))


@pytest.fixture
def step(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Модуль шага: compose и окружение стека — подставные, ядро скрипта выката — модуль тестов
    ядра (`deploy_fakes.tool`)."""
    module = importlib.import_module("stack_oracle_deploy")
    monkeypatch.setattr(module, "compose_base", lambda _run: ["compose", "-p", NAME])
    monkeypatch.setattr(module, "stack_env", lambda _run: {"KEEP": "yes"})
    monkeypatch.setattr(module, "load_core", lambda _run: tool)
    return module


@pytest.fixture
def run_of(step: ModuleType, monkeypatch: pytest.MonkeyPatch) -> Callable[..., SimpleNamespace]:
    """Прогон: машина — стек прогона, сервер соседа по разведке и наш стек; разбор конфига —
    верные ответы, кроме названных; коды адреса web — до и после; имена стека — до и после."""

    def make(
        *,
        machine: list[str] | None = None,
        after: list[str] | None = None,
        names: tuple[list[str], list[str]] = (NAMES, NAMES),
        codes: tuple[int, int] = (200, 200),
        **answers: tuple[Any, ...],
    ) -> SimpleNamespace:
        lines = STACK + rec.server_machine() + rec.ours() if machine is None else machine
        runner = FakeRunner(lines)
        asked: list[str] = []
        left = list(codes)  # копии: данные `parametrize` не съедать

        def probe(url: str, *_args: Any) -> int:
            asked.append(url)
            return left.pop(0)

        monkeypatch.setattr(tool, "Runner", lambda: runner)
        monkeypatch.setattr(tool, "http_code", probe)
        full = {"api": (0, GOOD.format("api")), "migrate": (0, GOOD.format("migrate"))}
        full |= {"refusal": (1, REFUSAL)} | answers
        docker = Stack(runner, full, after, [list(part) for part in names])
        facts = {"slot": (PORT, "172.16.0.0/28")}
        return SimpleNamespace(
            docker=docker, name=NAME, invented=(INVENTED,), facts=facts, asked=asked
        )

    return make


def test_a186_a189_snapshot_config_check_and_the_stack_untouched(
    step: ModuleType, run_of: Callable[..., SimpleNamespace]
) -> None:
    """A186, A189: снимок соседа видит стек прогона «соседом»; разбор конфига — у api в живом
    контейнере, у migrate разовым, оба годны в бою; отказ импорта у migrate — `-e ИМЯ`, значение
    — в окружении команды; снимок после — стек не тронут; адрес web спрошен до и после."""
    run = run_of()
    assert step.step_deploy_on_stack(run) == LINE
    assert [args[3:] for args, _ in run.docker.calls] == [
        ("exec", "-T", "api", *CHECK, "api"),
        ("run", "--rm", "-T", "--no-deps", "migrate", *CHECK, "migrate"),
        ("run", "--rm", "-T", "--no-deps", "-e", "DB_POOL_SIZE", "migrate", *CHECK, "migrate"),
    ]
    for args, env in run.docker.calls:
        assert args[:3] == ("compose", "-p", NAME) and env["KEEP"] == "yes"
        assert not [arg for arg in args if VALUE in arg]
    assert [env.get("DB_POOL_SIZE") for _, env in run.docker.calls] == [None, None, VALUE]
    assert run.asked == [f"http://127.0.0.1:{PORT}/"] * 2


def without(name: str) -> list[str]:
    return [line for line in STACK if f'"/{name}"' not in line]


ONE_OFF = f"{NAME}-migrate-run-0a1b2c3d4e5f"
LEFT = [*STACK, rec.inspect_line(ONE_OFF, NAME, status="exited", health=None)]
API = f"{NAME}-api-1"
RESTARTED = [rec.vary(line, restarts=1) if f'"/{API}"' in line else line for line in STACK]
CASES: list[tuple[str, dict[str, Any], str]] = [
    ("api-код", {"api": (1, GOOD.format("api"))}, "api: код 1, вывод — не «годен, режим prod»"),
    (
        "код-при-годном",
        {"migrate": (1, GOOD.format("migrate"), "Error response from daemon: removal\n")},
        "migrate: код 1, вывод — годен, режим prod",
    ),
    (
        "api-не-бой",
        {"api": (0, GOOD.format("api").replace("prod", "dev"))},
        "api: код 0, вывод — не «годен",
    ),
    ("migrate-чужой", {"migrate": (0, GOOD.format("api"))}, "migrate: код 0, вывод — не «годен"),
    ("отказ-код-0", {"refusal": (0, REFUSAL)}, "отказ импорта DB_POOL_SIZE: код не 1"),
    ("без-причины", {"refusal": (1, REFUSAL.replace(WHY, "что-то"))}, f"нет «{WHY}»"),
    ("процесс", {"refusal": (1, REFUSAL.replace("«migrate»", "«api»"))}, "процесс не назван"),
    ("Traceback", {"refusal": (1, "Traceback (most recent call last):\n" + REFUSAL)}, "Traceback"),
    ("input_value", {"refusal": (1, REFUSAL + f"input_value='{VALUE}'\n")}, "input_value в выводе"),
    ("значение", {"refusal": (1, REFUSAL + f"получено {VALUE}\n")}, "значение подмены в выводе"),
    ("секрет", {"refusal": (1, REFUSAL + INVENTED + "\n")}, "секрет в выводе"),
    (
        "стек-не-виден",
        {"machine": rec.server_machine() + rec.ours()},
        "снимок соседа не видит стек прогона: контейнеров 5, в снимке 0",
    ),
    ("web-не-200", {"codes": (502, 502)}, "адрес web — код 502, нужен 200"),
    ("перезапуск", {"after": RESTARTED}, "стек тронут: снимок соседа: общих 5, перезапусков 1"),
    (
        "разовый-остался",
        {"after": LEFT, "names": (NAMES, [*NAMES, ONE_OFF])},
        "коды прежние; пропало 0, новых 1; контейнеров стека 5 → 6",
    ),
    (
        "контейнер-пропал",
        {"after": without(f"{NAME}-web-1"), "names": (NAMES, NAMES[:-1])},
        "пропало 1, новых 0; контейнеров стека 5 → 4",
    ),
    ("код-web", {"codes": (200, 0)}, "кодов изменилось 1"),
    ("имена-разошлись", {"names": (NAMES, [*NAMES, ONE_OFF])}, "контейнеров стека 5 → 6"),
    (
        "снимок-без-web",
        {"after": without(f"{NAME}-web-1")},
        "пропало 1, новых 0; контейнеров стека 5 → 5",
    ),
]


@pytest.mark.parametrize(("kwargs", "why"), [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_a186_a189_step_red(
    step: ModuleType, run_of: Callable[..., SimpleNamespace], kwargs: dict[str, Any], why: str
) -> None:
    """A186, A189: красное шага — разбор конфига не годен или не в бою, отказ импорта с кодом не 1,
    без причины, без процесса, с `Traceback`, `input_value`, значением подмены или секретом;
    снимок не видит стек, адрес web не 200; после — перезапуск, разовый остался, контейнер
    пропал, код адреса сменился, имена стека разошлись. Значения подмены в причине нет."""
    run = run_of(**kwargs)
    with pytest.raises(compose_mod.StepError, match=re.escape(why)) as red:
        step.step_deploy_on_stack(run)
    assert VALUE not in str(red.value) and INVENTED not in str(red.value)


def test_a186_foreign_containers_of_the_machine_are_not_judged_by_the_step(
    step: ModuleType, run_of: Callable[..., SimpleNamespace]
) -> None:
    """A186: чужой контейнер машины перезапустился между снимками — шаг зелёный: он судит только
    стек прогона, на машине разработчика рядом живут чужие стеки."""
    neighbor = rec.server_machine()
    neighbor[0] = rec.vary(neighbor[0], restarts=1)
    run = run_of(after=STACK + neighbor + rec.ours())
    assert step.step_deploy_on_stack(run) == LINE


def test_a186_core_comes_from_the_copy_of_the_run() -> None:
    """A186: ядро скрипта выката — из копии прогона, модулем по пути: снимок и сравнение есть."""
    module = importlib.import_module("stack_oracle_deploy")
    core = module.load_core(SimpleNamespace(copy=dbtools.ROOT))
    assert Path(core.__file__) == dbtools.ROOT / "deploy" / "deploy.py"
    assert callable(core.neighbor_snapshot) and callable(core.compare)


def test_a186_core_missing_in_the_copy_is_red(tmp_path: Path) -> None:
    """A186: ядра в копии нет — красное с путём в копии, а не сбой загрузки."""
    module = importlib.import_module("stack_oracle_deploy")
    with pytest.raises(compose_mod.StepError, match="ядра скрипта выката нет в копии: deploy/"):
        module.load_core(SimpleNamespace(copy=tmp_path))


def test_a186_a189_step_on_the_live_stack_after_web_and_before_hardening() -> None:
    """A186, A189: шаг — на живом стеке после шагов web (адрес web отвечает) и до шагов защиты
    стека; в таблице один, с номерами A186 и A189; следующим шагам он не нужен."""
    found = [s for s in oracle.STEPS if s[2].__name__ == "step_deploy_on_stack"]
    assert [(numbers, needed) for numbers, _, _, needed in found] == [("A186 A189", False)]
    at = oracle.STEPS.index(found[0])
    assert oracle.STEPS[at - 1] == web.WEB_STACK_STEPS[-1]
    assert oracle.STEPS[at + 1] == hard.HARDENING_STEPS[0]
