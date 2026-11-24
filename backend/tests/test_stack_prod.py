"""Боевые правила стека (A89, первая и вторая строки) над слитым выводом основного файла с
боевым наложением `docker compose config --no-interpolate --format json`: лимиты, журнал
journald с меткой сервиса, потолок Redis без вытеснения; включаются флагом `--prod`.

Значения — как в файле: в выводе `--no-interpolate` compose 5.7.0 `mem_limit` — строка (`1g`),
`cpus` — число или строка, `command` — список или строка (замер 2026-11-24, decisions Ч1.5в).
Каждая проба — ровно своё нарушение: без правила проба проходит, и тест красный.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from collections.abc import Callable
from typing import Any

import pytest
from test_stack_rules import CODES, drop, put, svc, valid

from ai_geo_audit.ops import stack_prod as prod_rules
from ai_geo_audit.ops import stack_rules as sr

#: Лимиты наложения у каждого сервиса — числа decisions Ч1.5в, срез 0.
LIMITS = {
    "db": ("1g", 1),
    "redis": ("256m", 0.5),
    "migrate": ("256m", 1),
    "api": ("512m", 1),
    "web": ("128m", 0.5),
}
REDIS = ["redis-server", "--maxmemory", "100mb", "--maxmemory-policy", "noeviction"]
Found = set[tuple[str, str | None, str | None]]


def prod() -> dict[str, Any]:
    """Верный слитый вывод: основной стек и боевое наложение."""
    stack = valid()
    for name, (memory, cpus) in LIMITS.items():
        journal = {"driver": "journald", "options": {"tag": f"ai-geo-audit-{name}"}}
        stack["services"][name] |= {"mem_limit": memory, "cpus": cpus, "logging": journal}
    stack["services"]["redis"]["command"] = list(REDIS)
    stack["services"]["db"]["command"] = ["postgres", "-c", "max_connections=50"]
    return stack


def found(config: dict[str, Any]) -> Found:
    return {(v.code, v.service, v.variable) for v in sr.check(config, prod=True)}


def journal(name: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    return lambda config: config["services"][name]["logging"]


def options(name: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    return lambda config: config["services"][name]["logging"]["options"]


def run_main(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], text: str, *argv: str
) -> tuple[int, str]:
    """Вход модуля как у оракула: JSON на stdin, флаги — аргументами; код и вывод."""
    monkeypatch.setattr("sys.stdin", io.StringIO(text))
    code = sr.main(list(argv))
    return code, capsys.readouterr().out


def python_m(text: str) -> tuple[int, str, str]:
    """Настоящий вход шага A89 в образе — `python -m ai_geo_audit.ops.stack_rules --prod`, JSON на
    stdin: модуль правил тогда — `__main__`. Код, вывод и поток ошибок."""
    argv = [sys.executable, "-m", "ai_geo_audit.ops.stack_rules", "--prod"]
    done = subprocess.run(argv, input=text, capture_output=True, text=True, check=False, timeout=60)
    return done.returncode, done.stdout, done.stderr


def command(*words: str) -> Callable[[dict[str, Any]], None]:
    return put(svc("redis"), "command", ["redis-server", *words])


# Проба, код и сервис: ожидается ровно это нарушение.
PROBES = [
    (drop(svc("db"), "mem_limit"), "limits", "db"),
    (drop(svc("api"), "cpus"), "limits", "api"),
    (put(svc("web"), "mem_limit", "0"), "limits", "web"),
    (put(svc("redis"), "cpus", 0), "limits", "redis"),
    (put(svc("migrate"), "mem_limit", ""), "limits", "migrate"),
    (put(journal("api"), "driver", "json-file"), "journald", "api"),
    (drop(svc("migrate"), "logging"), "journald", "migrate"),
    (drop(options("db"), "tag"), "journald", "db"),
    (put(options("web"), "tag", "ai-geo-audit-api"), "journald", "web"),
    (put(options("api"), "tag", "{{.Name}}"), "journald", "api"),
    (drop(svc("redis"), "command"), "redis-memory", "redis"),
    (command("--maxmemory-policy", "noeviction"), "redis-memory", "redis"),
    (command("--maxmemory", "0", "--maxmemory-policy", "noeviction"), "redis-memory", "redis"),
    (command("--maxmemory", "100mb", "--maxmemory-policy", "allkeys-lru"), "redis-memory", "redis"),
    (command("--maxmemory", "100mb"), "redis-memory", "redis"),
    (command("--maxmemory-policy", "noeviction", "--maxmemory"), "redis-memory", "redis"),
]
PROBE_IDS = [
    "db-без-памяти",
    "api-без-cpus",
    "web-память-0",
    "redis-cpus-0",
    "migrate-память-пусто",
    "api-json-file",
    "migrate-без-журнала",
    "db-без-метки",
    "web-чужая-метка",
    "api-метка-шаблоном",
    "redis-без-команды",
    "redis-без-потолка",
    "redis-потолок-0",
    "redis-вытеснение",
    "redis-без-политики",
    "redis-потолок-без-значения",
]


def test_a89_valid_prod_stack_has_no_violations() -> None:
    """A89, первая строка: верный слитый вывод — ноль нарушений и основного, и боевого набора."""
    assert found(prod()) == set()


@pytest.mark.parametrize(("spoil", "code", "service"), PROBES, ids=PROBE_IDS)
def test_a89_each_prod_rule_has_its_probe(spoil: Callable, code: str, service: str) -> None:
    """A89, первая строка: `limits` — нет `mem_limit` или `cpus` (пусто и ноль — тоже нет);
    `journald` — журнал не journald или без метки своего сервиса; `redis-memory` — нет
    `--maxmemory` (или 0) или вытеснение не `noeviction`: ровно её нарушение с именем сервиса."""
    config = prod()
    spoil(config)
    assert found(config) == {(code, service, None)}


CALM = [
    put(svc("redis"), "command", " ".join(REDIS)),
    command("--save", "", "--maxmemory", "100mb", "--maxmemory-policy", "noeviction"),
    put(svc("api"), "cpus", "1"),
    put(svc("db"), "mem_limit", "1G"),
    put(svc("web"), "mem_limit", "134217728"),
    put(options("api"), "labels", "service"),
    command("--requirepass", "${REDIS_PASSWORD:?пароль}", *REDIS[1:]),
]


@pytest.mark.parametrize(
    "change",
    CALM,
    ids=[
        "команда-строкой",
        "другие-флаги",
        "cpus-строкой",
        "память-1G",
        "память-в-байтах",
        "ещё-опция",
        "ссылка-в-другом-слове",
    ],
)
def test_a89_neighbour_cases_are_not_violations(change: Callable) -> None:
    """A89, первая строка, соседние случаи (L53): команда Redis строкой, как в файле, и с другими
    флагами, `cpus` строкой, память `1G` и в байтах, другая опция журнала рядом с меткой, ссылка
    `${…}` в другом слове команды (пароль очереди «Учёток хранилищ») — не нарушения."""
    config = prod()
    change(config)
    assert found(config) == set()


def test_a89_prod_codes_follow_the_main_set_in_order() -> None:
    """A89: боевой набор — 24 кода основного и три боевых, по порядку."""
    assert (*CODES, "limits", "journald", "redis-memory") == sr.PROD_RULES


def test_a89_prod_rules_run_only_under_the_flag(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A89, вторая строка: без флага — основной набор, «правил 24», лимитов нет — не нарушение,
    и у `run()` по умолчанию тоже; с `--prod` на верном слитом выводе — «сервисов 5, правил 27,
    нарушений 0»."""
    plain = json.dumps(valid())
    assert sr.run(plain) == (0, ["итог: сервисов 5, правил 24, нарушений 0"])
    assert run_main(monkeypatch, capsys, plain) == (0, "итог: сервисов 5, правил 24, нарушений 0\n")
    summary = "итог: сервисов 5, правил 27, нарушений 0\n"
    assert run_main(monkeypatch, capsys, json.dumps(prod()), "--prod") == (0, summary)
    code, out = run_main(monkeypatch, capsys, plain, "--prod")
    assert code == 1
    assert out.splitlines()[-1] == "итог: сервисов 5, правил 27, нарушений 11"


def test_a89_violation_lines_name_the_rule_and_the_service(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A89, первая строка: строка нарушения — код правила, сервис и что не так; нет обоих
    лимитов — оба через «и»."""
    config = prod()
    del config["services"]["db"]["mem_limit"]
    del config["services"]["db"]["cpus"]
    config["services"]["api"]["logging"]["driver"] = "json-file"
    del config["services"]["redis"]["command"]
    code, out = run_main(monkeypatch, capsys, json.dumps(config), "--prod")
    assert code == 1
    assert out.splitlines() == [
        "limits: сервис db — нет mem_limit и cpus",
        "journald: сервис api — журнал не journald с меткой ai-geo-audit-api",
        "redis-memory: сервис redis — нет --maxmemory или вытеснение не noeviction",
        "итог: сервисов 5, правил 27, нарушений 3",
    ]


@pytest.mark.parametrize("argv", [["--prd"], ["--prod", "--prod"], ["prod"]])
def test_a89_unknown_flag_is_refused(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    """A89: флаг — только `--prod`; другой или лишний — отказ с кодом 2, а не основной набор."""
    code, out = run_main(monkeypatch, capsys, json.dumps(prod()), *argv)
    assert code == 2
    assert out.startswith("отказ: ")


def test_a89_redis_memory_without_redis_has_nothing_to_judge() -> None:
    """A89: в стеке нет сервиса redis — правилу `redis-memory` судить нечего: ни нарушения, ни
    отказа."""
    config = prod()
    del config["services"]["redis"]
    assert list(prod_rules.redis_memory(sr.parse(config))) == []


#: Хвост отказа на значении ссылкой и флаги Redis — литералами, как строки нарушений.
REF = "— ссылка ${…}, значение на сервере неизвестно"
FLAGS = "--maxmemory или --maxmemory-policy"
# Порча и строка отказа после «отказ: сервис »: значение ссылкой и команда без слов оболочки.
REFUSALS = [
    (put(svc("api"), "mem_limit", "${API_MEMORY:-512m}"), f"api: mem_limit {REF}"),
    (put(svc("web"), "cpus", "${WEB_CPUS}"), f"web: cpus {REF}"),
    (
        command("--maxmemory", "${REDIS_MAX:-100mb}", "--maxmemory-policy", "noeviction"),
        f"redis: {FLAGS} {REF}",
    ),
    (command("--maxmemory", "100mb", "--maxmemory-policy", "${POLICY}"), f"redis: {FLAGS} {REF}"),
    (
        put(svc("redis"), "command", 'redis-server --maxmemory 100mb --save "'),
        "redis: command не делится на слова оболочки",
    ),
]
REFUSAL_IDS = ["память-ссылкой", "cpus-ссылкой", "потолок-ссылкой", "политика-ссылкой", "кавычка"]


@pytest.mark.parametrize(("spoil", "reason"), REFUSALS, ids=REFUSAL_IDS)
def test_a89_value_unknown_until_the_server_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    spoil: Callable,
    reason: str,
) -> None:
    """A89: лимит или значение флага Redis ссылкой `${…}` — на сервере значение неизвестно, и
    команда, которую не разделить на слова оболочки, — отказ с кодом 2, как у `stop-grace`;
    строка отказа — целиком."""
    config = prod()
    spoil(config)
    out = f"отказ: сервис {reason}\n"
    assert run_main(monkeypatch, capsys, json.dumps(config), "--prod") == (2, out)


@pytest.mark.parametrize(
    ("spoil", "code", "line"),
    [
        (lambda config: None, 0, "итог: сервисов 5, правил 27, нарушений 0"),
        *((spoil, 2, f"отказ: сервис {reason}") for spoil, reason in REFUSALS),
    ],
    ids=["верный", *REFUSAL_IDS],
)
def test_a89_python_m_entry_as_in_the_image(spoil: Callable, code: int, line: str) -> None:
    """A89, вторая строка, находка 4 приёмки: настоящий вход — `python -m … --prod`, как его зовёт
    шаг A89 в образе. Модуль правил тогда — `__main__`, а отказ боевых правил — тот же класс:
    строка отказа целиком, код 2 и пустой поток ошибок, а не трассировка с кодом 1; на верном
    слитом выводе — «правил 27»: флаг доходит до входа."""
    config = prod()
    spoil(config)
    assert python_m(json.dumps(config)) == (code, f"{line}\n", "")
