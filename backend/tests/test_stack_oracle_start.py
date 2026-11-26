"""Шаг отказа старта (A173) без Docker: разовые контейнеры с подменой одной переменной — ответы
по сервису и переменной; значение подмены — только в окружении команды (L84). У каждой ветки —
свой тест (L88). Модули `scripts/` лежат вне пакета."""

from __future__ import annotations

import importlib
import subprocess
import sys
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote

import dbtools
import pytest

sys.path.insert(0, str(dbtools.ROOT / "scripts"))
start = importlib.import_module("stack_oracle_start")
compose_mod = importlib.import_module("stack_oracle_compose")

SUPER, JWT, MIGRATE = "super-value-1234567890", "jwt-value-1234567890", "mig@pass-value-123456"
APP, QUEUE = "app-value-1234567890", "queue-value-1234567890"
#: Выдуманные секреты прогона: пароль миграций — и в процентной записи (`Run.invented`).
INVENTED = (SUPER, JWT, MIGRATE, quote(MIGRATE, safe=""), APP, QUEUE)
#: Причины отказа по сервису и переменной (A173).
REASONS = {
    ("migrate", "DATABASE_URL"): "в адресе чужая роль базы — нужна audit_migrate",
    ("api", "DATABASE_URL"): "в адресе чужая роль базы — нужна audit_app",
    ("api", "REDIS_URL"): "пароль очереди короче 12 знаков",
}


def refusal(service: str, name: str, why: str | None = None) -> str:
    """Вывод отказа, как у `check_startup`: процесс, переменная и причина."""
    reason = why or REASONS[service, name]
    return f"Процесс «{service}» не стартует в боевом режиме:\n  {name} — {reason}\n"


class Once:
    """Подставной `docker`: ответ разовому контейнеру — по сервису и переменной; журнал —
    аргументы и окружение."""

    def __init__(self, answers: dict[tuple[str, str], tuple[int, str] | Exception]) -> None:
        self.answers = answers
        self.calls: list[tuple[tuple[str, ...], dict[str, str]]] = []

    def result(
        self, *args: str, env: dict[str, str], **kw: Any
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls.append((args, env))
        found = self.answers[args[-1], args[-2]]
        if isinstance(found, Exception):  # команда не уложилась в предел — отказ исполнителя
            raise found
        code, text = found
        return subprocess.CompletedProcess(["docker"], code, b"", text.encode())


@pytest.fixture
def run_of(monkeypatch: pytest.MonkeyPatch) -> Callable[..., SimpleNamespace]:
    """Прогон с подставным `docker`: верные отказы, кроме названных в `answers`; ревизии — по
    очереди, команда подставного `alembic` — в `asked`."""

    def make(
        revisions: list[list[str]] | None = None, **answers: tuple[int, str] | Exception
    ) -> SimpleNamespace:
        spoiled = {tuple(key.split("_", 1)): value for key, value in answers.items()}
        full = {key: (1, refusal(*key)) for key in REASONS} | spoiled
        docker = Once(full)
        left = list(revisions or [["0002"], ["0002"]])  # копия: данные `parametrize` не съедать
        asked: list[str] = []

        def revision(run: object, command: str) -> list[str]:
            asked.append(command)
            return left.pop(0)

        monkeypatch.setattr(start, "compose_base", lambda run: ["compose", "-p", "stack"])
        monkeypatch.setattr(start, "stack_env", lambda run: {"KEEP": "yes"})
        monkeypatch.setattr(start, "alembic", revision)
        return SimpleNamespace(
            docker=docker,
            app_password=APP,
            migrate_password=MIGRATE,
            invented=INVENTED,
            asked=asked,
        )

    return make


ADDRESSES = {
    ("migrate", "DATABASE_URL"): compose_mod.DB_URL.format("audit_app", APP),
    ("api", "DATABASE_URL"): compose_mod.DB_URL.format(
        "audit_migrate", MIGRATE.replace("@", "%40")
    ),
    ("api", "REDIS_URL"): "redis://redis:6379/0",
}


def test_a173_step_runs_three_refusals_and_keeps_the_revision(
    run_of: Callable[..., SimpleNamespace],
) -> None:
    """A173: три разовых контейнера — migrate с адресом роли сервиса, api с адресом роли миграций и
    с адресом очереди без пароля; `run -e ИМЯ` без значения, значение — в окружении команды;
    ревизия до и после — `alembic current`."""
    run = run_of()
    line = start.step_refusal(run)
    assert line == (
        "отказ старта: migrate — DATABASE_URL, код 1; api — DATABASE_URL, код 1; api — REDIS_URL,"
        " код 1; адресов, паролей и Traceback нет; ревизия прежняя"
    )
    assert run.asked == ["current", "current"]
    calls = [(args[-1], args[-2], env) for args, env in run.docker.calls]
    assert [(service, name) for service, name, _ in calls] == list(REASONS)
    for (args, env), key in zip(run.docker.calls, REASONS, strict=True):
        assert args[:3] == ("compose", "-p", "stack")
        assert args[3:8] == ("run", "--rm", "-T", "--no-deps", "-e")
        assert env[key[1]] == ADDRESSES[key] and env["KEEP"] == "yes"
        assert not [word for word in args if any(secret in word for secret in INVENTED)]


SPOILS: list[tuple[str, dict[str, Any], str]] = [
    ("migrate-code-0", {"migrate_DATABASE_URL": (0, refusal("migrate", "DATABASE_URL"))}, "код 0"),
    (
        "no-reason",
        {"api_REDIS_URL": (1, refusal("api", "REDIS_URL", "что-то другое"))},
        "нет причины «REDIS_URL — пароль очереди короче 12 знаков»",
    ),
    (
        "process-not-named",
        {
            "api_DATABASE_URL": (
                1,
                f"Процесс не стартует:\n  DATABASE_URL — {REASONS['api', 'DATABASE_URL']}\n",
            )
        },
        "процесс не назван",
    ),
    (
        "traceback",
        {
            "migrate_DATABASE_URL": (
                1,
                "Traceback (most recent call last):\n" + refusal("migrate", "DATABASE_URL"),
            )
        },
        "Traceback в выводе",
    ),
    (
        "password-in-output",
        {"migrate_DATABASE_URL": (1, refusal("migrate", "DATABASE_URL") + APP)},
        "адрес или пароль в выводе",
    ),
    (
        "encoded-password-in-output",
        {"api_DATABASE_URL": (1, refusal("api", "DATABASE_URL") + INVENTED[3])},
        "адрес или пароль в выводе",
    ),
    (
        "address-in-output",
        {"api_REDIS_URL": (1, refusal("api", "REDIS_URL") + "redis://redis:6379/0")},
        "адрес или пароль в выводе",
    ),
    (
        "host-in-output",
        {"api_DATABASE_URL": (1, refusal("api", "DATABASE_URL") + "connect to db:5432")},
        "адрес или пароль в выводе",
    ),
]


@pytest.mark.parametrize(
    ("answers", "reason"),
    [(answers, reason) for _, answers, reason in SPOILS],
    ids=[name for name, *_ in SPOILS],
)
def test_a173_step_is_red_on_each_branch(
    run_of: Callable[..., SimpleNamespace], answers: dict[str, Any], reason: str
) -> None:
    """A173, ветки: код 0, нет причины или процесса, `Traceback`, пароль, адрес или узел в выводе —
    красное с сервисом, переменной и причиной; лишнего в тексте нет."""
    (key,) = answers
    with pytest.raises(compose_mod.StepError) as red:
        start.step_refusal(run_of(**answers))
    service, name = key.split("_", 1)
    assert str(red.value) == f"{service}, {name}: {reason}"


def test_a173_two_problems_of_one_container_are_both_named(
    run_of: Callable[..., SimpleNamespace],
) -> None:
    """A173: у одного контейнера — и код 0, и пароль в выводе: красное называет обе причины."""
    with pytest.raises(compose_mod.StepError) as red:
        start.step_refusal(
            run_of(migrate_DATABASE_URL=(0, refusal("migrate", "DATABASE_URL") + SUPER))
        )
    assert str(red.value) == "migrate, DATABASE_URL: код 0; адрес или пароль в выводе"


def test_a173_limit_names_the_case(run_of: Callable[..., SimpleNamespace]) -> None:
    """A173: разовый контейнер не уложился в предел — красное с сервисом и переменной случая и
    причиной исполнителя."""
    late = compose_mod.StepError("docker compose -p не уложился в 120 с")
    with pytest.raises(compose_mod.StepError) as red:
        start.step_refusal(run_of(api_REDIS_URL=late))
    assert str(red.value) == "api, REDIS_URL: docker compose -p не уложился в 120 с"


def test_a173_changed_revision_is_red(run_of: Callable[..., SimpleNamespace]) -> None:
    """A173: ревизия после отказов другая — красное: миграции шли до отказа."""
    with pytest.raises(compose_mod.StepError) as red:
        start.step_refusal(run_of(revisions=[["0002"], ["0003"]]))
    assert str(red.value) == "alembic current — не прежний"


def test_a173_step_is_in_the_table_before_the_breaking_step() -> None:
    """A173: шаг — в таблице оракула один, с номером A173, до A67, который ломает стек."""
    oracle = importlib.import_module("stack_oracle")
    (step,) = [step for step in oracle.STEPS if step[0] == "A173"]
    assert step in start.START_STEPS and step[2] is start.step_refusal
    assert oracle.STEPS.index(step) < oracle.STEPS.index(compose_mod.BREAKING_STEPS[0])
