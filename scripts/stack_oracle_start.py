"""Шаг оракула «отказ старта» (A173): разовые контейнеры процессов с подменой одной переменной —
адрес базы с чужой ролью у migrate и у api, адрес очереди без пароля у api. В бою каждый выходит с
кодом не 0 до работы и называет процесс, переменную и причину; ни адреса, ни пароля, ни `Traceback`
в выводе нет, миграции не идут. Подмена — `run -e ИМЯ` без значения, значение — в окружении
команды (L84): в её строке его нет.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from stack_oracle_compose import DB_URL, StepError, alembic, compose_base, output, stack_env

if TYPE_CHECKING:
    from stack_oracle import Run

#: Сервис, переменная и причина отказа; подменённое значение строит `cases`.
Case = tuple[str, str, str, str]
#: Что в выводе отказа быть не должно, кроме паролей: узлы адресов базы и очереди.
HOSTS = ("db:5432", "redis:6379")


def cases(run: Run) -> list[Case]:
    """Сервис, переменная, подменённое значение и причина: чужая роль базы — у migrate адрес
    сервиса и у api адрес миграций, адрес очереди без пароля — у api."""
    migrate = DB_URL.format("audit_migrate", run.migrate_password.replace("@", "%40"))
    return [
        (
            "migrate",
            "DATABASE_URL",
            DB_URL.format("audit_app", run.app_password),
            "в адресе чужая роль базы — нужна audit_migrate",
        ),
        ("api", "DATABASE_URL", migrate, "в адресе чужая роль базы — нужна audit_app"),
        ("api", "REDIS_URL", "redis://redis:6379/0", "пароль очереди короче 12 знаков"),
    ]


def refused(run: Run, service: str, name: str, value: str) -> tuple[int, str]:
    """Разовый контейнер сервиса с подменённой переменной: код выхода и вывод; значение — в
    окружении команды, в строке — только имя (L84)."""
    env = stack_env(run) | {name: value}
    once = ("run", "--rm", "-T", "--no-deps", "-e", name, service)
    done = run.docker.result(*compose_base(run), *once, env=env)
    return done.returncode, output(done)


def step_refusal(run: Run) -> str:
    """A173: каждый разовый контейнер — код не 0, в выводе — процесс, переменная и причина; ни
    адреса, ни пароля, ни `Traceback`; `alembic current` до и после — одна ревизия."""
    before = alembic(run, "current")
    seen: list[str] = []
    for service, name, value, why in cases(run):
        code, text = refused(run, service, name, value)
        problems = ["код 0"] * (code == 0)
        problems += [f"нет причины «{name} — {why}»"] * (f"{name} — {why}" not in text)
        problems += ["процесс не назван"] * (f"«{service}»" not in text)
        problems += ["Traceback в выводе"] * ("Traceback" in text)
        secret = any(word in text for word in (*run.invented, value, *HOSTS))
        problems += ["адрес или пароль в выводе"] * secret
        if problems:
            raise StepError(f"{service}, {name}: {'; '.join(problems)}")
        seen.append(f"{service} — {name}, код {code}")
    if alembic(run, "current") != before:
        raise StepError("alembic current — не прежний")
    return f"отказ старта: {'; '.join(seen)}; адресов, паролей и Traceback нет; ревизия прежняя"


#: Шаги отказа старта — на живом стеке, до A67, который его ломает.
START_STEPS: list[tuple[str, str, Callable[[Run], str], bool]] = [
    ("A173", "отказ старта: чужая роль базы и пароль очереди", step_refusal, False),
]
