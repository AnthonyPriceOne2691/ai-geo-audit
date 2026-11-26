"""Поиск выдуманных секретов оракула (A172, первая строка): пароли как есть и в процентной записи,
секрет подписи, три адреса и пароли шагов из маски — в сыром выводе каждой команды, журналах
сервисов, описаниях контейнеров без `Config.Env`, процессах и `config --no-interpolate`. Дважды: на
живом стеке перед A67, который снимает его с томами, и последним шагом.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from stack_oracle_compose import StepError, compose, stack_env

if TYPE_CHECKING:
    from stack_oracle import Run

SECRETS: tuple[str, ...] = ("DB_PASSWORD", "POSTGRES_MIGRATE_PASSWORD", "POSTGRES_APP_PASSWORD")
SECRETS += ("REDIS_PASSWORD", "JWT_SECRET")
ADDRESSES = ("MIGRATE_DATABASE_URL", "API_DATABASE_URL", "API_REDIS_URL")


def needles(run: Run) -> dict[str, str]:
    """Имя — значение: секреты и адреса стека, процентная запись, пароли шагов из маски."""
    env = stack_env(run)
    found = {name: env[name] for name in (*SECRETS, *ADDRESSES)}
    encoded = {name: quote(env[name], safe="") for name in SECRETS}
    found |= {f"{n} в процентной записи": v for n, v in encoded.items() if v != env[n]}
    steps = [secret for secret in dict.fromkeys(run.docker.mask) if secret not in found.values()]
    return found | {f"пароль шага {n}": secret for n, secret in enumerate(steps, 1)}


def bare(item: dict[str, Any]) -> str:
    """Описание контейнера без `Config.Env`: значения там — по замыслу."""
    config = {key: value for key, value in item.get("Config", {}).items() if key != "Env"}
    return json.dumps({**item, "Config": config}, ensure_ascii=False)


def said(args: tuple[str, ...], text: str) -> tuple[str, str]:
    """Место и текст сырого вывода: у compose — без имени стека и файлов, у `docker inspect` —
    описания без `Config.Env`; отказ inspect — текст как есть."""
    where = " ".join(args[args.index("--env-file") + 2 :] if "--env-file" in args else args)
    try:
        return where[:60], "\n".join(map(bare, json.loads(text))) if args[0] == "inspect" else text
    except ValueError:
        return where[:60], text


def texts(run: Run) -> dict[str, list[tuple[str, str]]]:
    """Тексты по видам: место — текст; сырой захват — до команд самого поиска."""
    raw = [said(args, text) for args, text in list(run.docker.raw)]
    services = compose(run, "config", "--services", limit="docker").split()
    logs = [
        (f"журнал {s}", compose(run, "logs", "--no-color", s, limit="docker")) for s in services
    ]
    ids = compose(run, "ps", "--all", "--quiet", limit="docker").split()
    items = json.loads(run.docker("inspect", *ids, limit="docker")) if ids else []
    config = compose(run, "config", "--no-interpolate", limit="docker")
    return {
        "сырой вывод": raw,
        "журналы": logs,
        "описания": [(f"описание {item.get('Name')}", bare(item)) for item in items],
        "процессы": [("compose top", compose(run, "top", limit="docker"))],
        "config": [("config --no-interpolate", config)],
    }


def step_search(run: Run) -> str:
    """A172, первая строка: ни одного секрета ни в одном тексте; в итоге — число текстов по видам
    и секретов; в причине — имя и место, без значения; ноль текстов вида — отказ, а не «чисто»."""
    wanted, kinds = needles(run), texts(run)
    if empty := [kind for kind, found in kinds.items() if not found]:
        raise StepError(f"текстов 0: {', '.join(empty)} — судить не по чему")
    pairs = [(where, text) for found in kinds.values() for where, text in found]
    hits = [
        f"{name} — {at}" for at, text in pairs for name, value in wanted.items() if value in text
    ]
    if hits:
        raise StepError(f"совпадений {len(hits)}: {'; '.join(hits[:6])}")
    counts = ", ".join(f"{kind} {len(found)}" for kind, found in kinds.items())
    return f"текстов {len(pairs)}: {counts}; секретов {len(wanted)}, совпадений 0"


LIVE_SEARCH = [("A172", "поиск секретов на живом стеке", step_search, False)]
FINAL_SEARCH = [("A172", "поиск секретов после всех шагов", step_search, False)]
