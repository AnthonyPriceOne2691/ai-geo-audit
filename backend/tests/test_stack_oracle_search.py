"""Поиск выдуманных секретов оракула (A172, первая строка) без Docker: сырой захват команд,
журналы, описания контейнеров без `Config.Env`, процессы и `config --no-interpolate`; в причине —
имя секрета и место, значений нет; у каждого места — своя порча (L88). Подставной compose знает
только верные команды: другой флаг — KeyError."""

from __future__ import annotations

import importlib
import json
import sys
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from urllib.parse import quote

import dbtools
import pytest

sys.path.insert(0, str(dbtools.ROOT / "scripts"))
search = importlib.import_module("stack_oracle_search")
compose_mod = importlib.import_module("stack_oracle_compose")

SUPER, JWT, APP, QUEUE, STEP = (
    f"{n}-value-123456" for n in ("super", "jwt", "app", "queue", "step")
)
MIG = "mig@value-123456"
MIGRATE_URL = compose_mod.DB_URL.format("audit_migrate", quote(MIG, safe=""))
API_URL = compose_mod.DB_URL.format("audit_app", APP)
QUEUE_URL = "redis://:" + QUEUE + "@redis:6379/0"
NAMES = (*search.SECRETS, *search.ADDRESSES)
STACK = dict(
    zip(NAMES, (SUPER, MIG, APP, QUEUE, JWT, MIGRATE_URL, API_URL, QUEUE_URL), strict=True)
)
#: Маска исполнителя: выдуманные секреты прогона и пароль шага A170.
MASK = [SUPER, JWT, MIG, quote(MIG, safe=""), APP, QUEUE, STEP]
ALEMBIC = ("compose", "-p", "s", "--env-file", "/dev/null", "run", "--rm", "migrate", "alembic")
SERVICES = ("db", "redis", "migrate", "api", "web")


def described(name: str, command: list[str]) -> dict[str, Any]:
    """Описание контейнера: значения — только в `Config.Env`, его поиск не смотрит."""
    env = [f"DATABASE_URL={API_URL}", f"REDIS_URL={QUEUE_URL}", f"JWT_SECRET={JWT}"]
    return {"Name": f"/s-{name}-1", "Config": {"Env": env, "Cmd": command}}


class Shown:
    """Подставной исполнитель: сырой захват и маска прогона; `inspect` — описания по id."""

    def __init__(self, where: dict[Any, Any]) -> None:
        self.where, self.raw, self.mask = where, where["raw"], list(MASK)
        self.asked: list[tuple[str, ...]] = []

    def __call__(self, *args: str, limit: str = "run") -> str:
        self.asked.append(args)
        return json.dumps([self.where["items"][name] for name in args[1:]])


@pytest.fixture
def run_of(monkeypatch: pytest.MonkeyPatch) -> Callable[..., SimpleNamespace]:
    """Прогон с подставными compose и исполнителем; `spoil` портит одно место до шага."""

    def make(spoil: Callable[[dict[Any, Any]], None] = lambda where: None) -> SimpleNamespace:
        where: dict[Any, Any] = {
            "raw": [
                (ALEMBIC, "abc123 (head)\n"),
                (("inspect", "c1"), json.dumps([described("api", ["uvicorn"])])),
                (("inspect", "gone"), "Error: No such object: gone\n"),
            ],
            "items": {"c1": described("api", ["uvicorn"]), "c2": described("redis", ["sh"])},
            ("config", "--services"): "\n".join(SERVICES),
            **{("logs", "--no-color", name): f"{name} started\n" for name in SERVICES},
            ("top",): "SERVICE  #  UID  PID  CMD\nredis  1  999  7  redis-server *:6379\n",
            ("config", "--no-interpolate"): "services:\n  redis:\n    user: redis\n",
        }
        spoil(where)
        where["ps", "--all", "--quiet"] = "\n".join(where["items"])
        monkeypatch.setattr(search, "compose", lambda run, *args, limit: str(where[args]))
        monkeypatch.setattr(search, "stack_env", lambda run: STACK | {"WEB_PORT": "18100"})
        return SimpleNamespace(docker=Shown(where))

    return make


def test_a172_search_counts_texts_and_finds_nothing(run_of: Callable[..., SimpleNamespace]) -> None:
    """A172, первая строка: сырой вывод каждой команды, журнал каждого сервиса, описание каждого
    контейнера без `Config.Env`, `compose top` и `config --no-interpolate`; секреты — четыре
    пароля и секрет подписи, пароль роли миграций и в процентной записи, три адреса, пароль шага
    из маски; совпадений нет. Итог — строкой целиком."""
    run = run_of()
    assert search.step_search(run) == (
        "текстов 12: сырой вывод 3, журналы 5, описания 2, процессы 1, config 1; секретов 10, "
        "совпадений 0"
    )
    assert run.docker.asked == [("inspect", "c1", "c2")]


def put(*path: Any, value: object) -> Callable[[dict[Any, Any]], None]:
    """Порча одного места: значение по пути ключей."""

    def spoil(where: dict[Any, Any]) -> None:
        for key in path[:-1]:
            where = where[key]
        where[path[-1]] = value

    return spoil


RUN = "run --rm migrate alembic"
SPOILS = [
    (
        "requirepass-one-dollar",
        put("items", "c2", "Config", "Cmd", value=["sh", "-c", f"requirepass {QUEUE}"]),
        "совпадений 1: REDIS_PASSWORD — описание /s-redis-1",
    ),
    (
        "migrate-prints-its-address",
        put("raw", 0, value=(ALEMBIC, MIGRATE_URL)),
        f"совпадений 2: MIGRATE_DATABASE_URL — {RUN}; POSTGRES_MIGRATE_PASSWORD в процентной "
        f"записи — {RUN}",
    ),
    (
        "address-in-a-log",
        put(("logs", "--no-color", "api"), value=API_URL),
        "совпадений 2: POSTGRES_APP_PASSWORD — журнал api; API_DATABASE_URL — журнал api",
    ),
    ("in-top", put(("top",), value=QUEUE), "совпадений 1: REDIS_PASSWORD — compose top"),
    (
        "in-config",
        put(("config", "--no-interpolate"), value=JWT),
        "совпадений 1: JWT_SECRET — config --no-interpolate",
    ),
    ("step-password", put("raw", 0, value=(ALEMBIC, STEP)), f"совпадений 1: пароль шага 1 — {RUN}"),
    (
        "raw-description-outside-env",
        put("raw", 1, value=(("inspect", "c1"), json.dumps([described("db", [SUPER])]))),
        "совпадений 1: DB_PASSWORD — inspect c1",
    ),
    ("no-raw", put("raw", value=[]), "текстов 0: сырой вывод — судить не по чему"),
    ("no-containers", put("items", value={}), "текстов 0: описания — судить не по чему"),
]


@pytest.mark.parametrize(("spoil", "why"), [s[1:] for s in SPOILS], ids=[s[0] for s in SPOILS])
def test_a172_secret_in_any_text_or_no_texts_is_red(
    run_of: Callable[..., SimpleNamespace], spoil: Callable, why: str
) -> None:
    """A172, первая строка: порчи «в строке `requirepass` один `$`» — пароль в описании redis — и
    «migrate печатает свой адрес» — адрес в сыром выводе; секрет в журнале, процессах, `config`,
    пароль шага, описание вне `Config.Env` в сыром захвате — красное с именем и местом, без
    значения; ноль текстов вида — отказ, а не «чисто»."""
    with pytest.raises(compose_mod.StepError) as red:
        search.step_search(run_of(spoil))
    assert str(red.value) == why
    assert not any(secret in why for secret in MASK)


def test_a172_search_runs_on_the_live_stack_and_after_all_steps() -> None:
    """A172, первая строка: поиск — на живом стеке перед A67, который снимает его с томами (журналы
    api живут до него), и последним шагом прогона."""
    oracle = importlib.import_module("stack_oracle")
    (live,), (final,) = search.LIVE_SEARCH, search.FINAL_SEARCH
    assert live[2] is final[2] is search.step_search
    assert oracle.STEPS.index(live) + 1 == oracle.STEPS.index(compose_mod.BREAKING_STEPS[0])
    assert oracle.STEPS[-1] == final
