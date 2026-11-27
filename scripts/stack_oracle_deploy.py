"""Шаг оракула «скрипт выката на живом стеке» (6а-2, T5; A186, A189): снимок соседа ядром
`deploy/deploy.py` копии прогона и разбор конфига `python -m ai_geo_audit.ops.config_check` внутри
собранного образа. Для скрипта выката стек прогона — «сосед»: метка проекта у него не
`ai-geo-audit`. Снимок до; разбор у api — в живом контейнере, у migrate — разовым, оба годны в бою;
отказ импорта у migrate — код 1, имя и причина, без `Traceback`, `input_value` и значения (L29,
L30); снимок после — стек шагом не тронут. Подмена — `run -e ИМЯ`, значение — в окружении
команды, в строке только имя (L84).
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
import sys
from collections.abc import Callable
from types import ModuleType
from typing import TYPE_CHECKING, Any

from stack_oracle_compose import StepError, compose_base, output, slot, stack_env

if TYPE_CHECKING:
    from stack_oracle import Run

CORE = "deploy/deploy.py"
CHECK = ("python", "-m", "ai_geo_audit.ops.config_check")
GOOD = re.compile(r"Конфиг процесса «(api|migrate)» годен, режим prod: проверено имён: \d+")
#: Отказ импорта: имя, значение подмены и причина — поле упавшего домена (A189, decisions).
#: Значение — число, которого нет в шуме compose: в имени разового контейнера 12 знаков hex.
BAD = ("DB_POOL_SIZE", "4000000001", "DB_POOL_SIZE — Input should be less than or equal to 50")


def load_core(run: Run) -> ModuleType:
    """Ядро скрипта выката из копии прогона — модулем по пути; только стандартная библиотека."""
    path = run.copy / CORE
    spec = importlib.util.spec_from_file_location("oracle_deploy_core", path)
    if not path.is_file() or spec is None or spec.loader is None:
        raise StepError(f"ядра скрипта выката нет в копии: {CORE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # класс данных со строковыми аннотациями ищет модуль здесь
    spec.loader.exec_module(module)
    return module


def stack_names(run: Run) -> list[str]:
    """Имена контейнеров стека прогона, и остановленных, — по метке проекта compose."""
    mark = f"label=com.docker.compose.project={run.name}"
    found = run.docker("ps", "--all", "--filter", mark, "--format", "{{.Names}}", limit="docker")
    return found.split()


def keys(names: list[str]) -> set[str]:
    """Ключи снимка соседа: начало sha256 имени — имён в снимке нет (A186)."""
    return {hashlib.sha256(name.encode()).hexdigest()[:16] for name in names}


def config_check(
    run: Run, service: str, swap: dict[str, str] | None = None
) -> tuple[int, str, str]:
    """Разбор конфига процесса в собранном образе: у api без подмены — в живом контейнере, иначе
    — разовым контейнером сервиса; подмена — `run -e ИМЯ`, значение — в окружении (L84). Ответ —
    код, stdout команды и весь вывод: годный судится по stdout — в stderr у `compose run` шум
    самого compose (проба 2026-11-26T13:16:40Z)."""
    swap = swap or {}
    if service == "api" and not swap:
        args: tuple[str, ...] = ("exec", "-T", "api", *CHECK, "api")
    else:
        names = tuple(part for name in swap for part in ("-e", name))
        args = ("run", "--rm", "-T", "--no-deps", *names, service, *CHECK, service)
    done = run.docker.result(*compose_base(run), *args, env=stack_env(run) | swap)
    return done.returncode, done.stdout.decode("utf-8", "replace"), output(done)


def refusal_problems(run: Run, code: int, text: str) -> list[str]:
    """Что не так с отказом импорта: код, причина, процесс, `Traceback`, значения."""
    _name, value, why = BAD
    problems = ["код не 1"] * (code != 1) + [f"нет «{why}»"] * (why not in text)
    problems += ["процесс не назван"] * ("«migrate»" not in text)
    problems += [f"{word} в выводе" for word in ("Traceback", "input_value") if word in text]
    problems += ["значение подмены в выводе"] * (value in text)
    return problems + ["секрет в выводе"] * any(secret in text for secret in run.invented)


def step_deploy_on_stack(run: Run) -> str:
    """A186, A189: снимок соседа до; разбор конфига у api и migrate — годен, режим prod; отказ
    импорта у migrate; снимок после — у контейнеров стека перезапусков 0, пропало 0, новых 0,
    коды адреса web прежние."""
    core = load_core(run)
    ctx = core.Context(runner=core.Runner(), probe=core.http_code)
    url = f"http://127.0.0.1:{slot(run)[0]}/"
    names = stack_names(run)
    before = core.neighbor_snapshot(ctx, url)
    if not names or not keys(names) <= before.seen.keys():
        seen = len(keys(names) & before.seen.keys())
        raise StepError(
            f"снимок соседа не видит стек прогона: контейнеров {len(names)}, в снимке {seen}"
        )
    if before.codes != (200,):
        raise StepError(f"адрес web — код {', '.join(map(str, before.codes))}, нужен 200")
    for service in ("api", "migrate"):
        code, said, _whole = config_check(run, service)
        good = GOOD.fullmatch(said.strip())
        fits = good is not None and good[1] == service
        if code or not fits:
            state = "годен, режим prod" if fits else "не «годен, режим prod»"
            raise StepError(f"{service}: код {code}, вывод — {state}")
    name, value, _why = BAD
    code, _said, whole = config_check(run, "migrate", {name: value})
    if problems := refusal_problems(run, code, whole):
        raise StepError(f"migrate, отказ импорта {name}: {'; '.join(problems)}")
    after_names = stack_names(run)
    after = core.neighbor_snapshot(ctx, url)
    ours = keys(names) | keys(after_names)
    result = core.compare(*(mine(core, shot, ours) for shot in (before, after)))
    clean = "перезапусков 0" in result.line and "пропало 0, новых 0" in result.line
    if result.red or not clean or sorted(after_names) != sorted(names):
        counts = f"контейнеров стека {len(names)} → {len(after_names)}"
        raise StepError(f"стек тронут: {result.line}; {counts}")
    return (
        "api и migrate — годен, режим prod; отказ импорта у migrate — код 1, без Traceback и "
        f"значения; {result.line}"
    )


def mine(core: ModuleType, shot: Any, ours: set[str]) -> Any:
    """Снимок только по контейнерам стека прогона: чужие контейнеры машины шаг не судит."""
    return core.Snapshot({k: v for k, v in shot.seen.items() if k in ours}, shot.codes)


#: Шаг скрипта выката — на живом стеке после шагов web (адрес web отвечает), до шагов защиты.
DEPLOY_STEPS: list[tuple[str, str, Callable[[Run], str], bool]] = [
    ("A186 A189", "скрипт выката: снимок соседа и разбор конфига", step_deploy_on_stack, False),
]
