"""Живые `env-init`, `put-secret` и `last` (6а-2, срез 4 и T5; A182, A183): скрипт шага джобы
`stack` грузится по пути. Без машины CI и без root — отказ словами до любой записи; файл владельца
— ровно имена «задаёт владелец» блока «сервер»; шаг стоит в джобе после оракула; `last` судится
по подставному ответу. Сам прогон — только в CI."""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml
from deploy_fakes import ROOT

SCRIPT = ROOT / "scripts" / "deploy_live.py"
WORKFLOW = ROOT / ".github" / "workflows" / "quality.yml"
STEP = "sudo --preserve-env=GITHUB_ACTIONS python3 scripts/deploy_live.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("deploy_live", SCRIPT)
    if spec is None or spec.loader is None:
        raise ImportError(f"скрипт живых прогонов не загружается: {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def no_copy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ModuleType:
    """Скрипт, у которого копия клона — красное: отказ обязан прийти раньше записи. Корни сервера
    — во временном каталоге, их ещё нет: тест не зависит от корней машины, где идёт."""
    live = load()

    def copy(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("запись до проверки машины")

    monkeypatch.setattr(live.shutil, "copytree", copy)
    monkeypatch.setattr(live, "SERVICE", tmp_path / "srv" / "ai-geo-audit")
    monkeypatch.setattr(live, "ETC", tmp_path / "etc" / "ai-geo-audit")
    return live


def tree(root: Path) -> list[str]:
    """Всё, что лежит под корнем, — и висячие ссылки: «ничего не записано» сверяется по нему."""
    return sorted(str(path.relative_to(root)) for path in root.rglob("*"))


@pytest.mark.parametrize("ci", [None, "false"], ids=["нет-переменной", "false"])
def test_a182_live_run_refuses_outside_ci(
    no_copy: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    ci: str | None,
) -> None:
    """A182: не на машине джобы CI — отказ с кодом 2 и словами: скрипт пишет в `/srv` и `/etc`;
    машина CI — только `GITHUB_ACTIONS=true`, не любое значение (мелочь приёмки среза 4). Root
    подставлен: отказ даёт сама эта защита. На диске ничего не появилось."""
    if ci is None:
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    else:
        monkeypatch.setenv("GITHUB_ACTIONS", ci)
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    before = tree(tmp_path)
    assert no_copy.main() == 2
    assert "отказ: только на машине джобы CI" in capsys.readouterr().err
    assert tree(tmp_path) == before


def test_a182_live_run_refuses_without_root(
    no_copy: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A182: на машине CI, но не от root — отказ с кодом 2: `env-init` ставит владельцев файлов."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(os, "geteuid", lambda: 1001)
    assert no_copy.main() == 2
    assert "отказ: нужен root — шаг джобы под sudo" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("root", "kind"),
    [("SERVICE", "каталог"), ("ETC", "каталог"), ("SERVICE", "ссылка"), ("ETC", "ссылка")],
)
def test_a182_live_run_refuses_on_a_machine_with_server_roots(
    no_copy: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    root: str,
    kind: str,
) -> None:
    """A182, находка 3 приёмки среза 4: на машине CI от root, но корень сервиса или свой `/etc`
    уже есть — каждый отдельно, каталогом или висячей ссылкой — отказ с кодом 2 «машина не
    чистая» до копии клона; ни один шаг не начат, на диске ничего не записано. На машине джобы
    корней не бывает: живой прогон эту защиту не проверит — только тест."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    path: Path = getattr(no_copy, root)
    path.parent.mkdir(parents=True)
    if kind == "каталог":
        path.mkdir()
    else:
        path.symlink_to(tmp_path / "нет")
    before = tree(tmp_path)
    assert no_copy.main() == 2
    out = capsys.readouterr()
    assert "отказ: корни сервера уже есть — машина не чистая" in out.err
    assert out.out == ""
    assert tree(tmp_path) == before


def test_a182_live_owner_file_is_exactly_the_owner_names_of_the_sample() -> None:
    """A182: выдуманный файл владельца живого прогона — ровно имена «задаёт владелец» блока
    «сервер» образца: новое имя владельца без значения в нём — красное здесь, а не в CI."""
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    block = text.split("## задаёт владелец\n", 1)[1].split("## ", 1)[0]
    names = re.findall(r"(?m)^# ([A-Z][A-Z0-9_]*)=", block)
    assert list(load().OWNER) == names


def test_a182_live_step_runs_after_the_oracle_in_the_stack_job() -> None:
    """A182, план «Исполнение рисковых путей»: шаг живых прогонов — в джобе `stack`, после
    оракула, под sudo с переменной машины CI."""
    steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["stack"]["steps"]
    runs = [step.get("run", "") for step in steps]
    oracle = runs.index("python3 scripts/stack_oracle.py")
    assert runs[oracle + 1 :] == [STEP]


def test_a183_live_steps_in_order_last_after_the_moves() -> None:
    """A182, A183: шаги живого прогона по порядку; `last` — после хода: поздний журнал — второго
    `env-init`, его `last` и обязан показать (имя журнала до микросекунд, находка T5)."""
    assert [title for title, _ in load().STEPS] == [
        "A182 env-init на чистых корнях",
        "A182 put-secret файла владельца",
        "A182 второй env-init",
        "A183 дерево сервиса чистое",
        "A182 compose принимает файл сервера",
        "A183 last после хода",
    ]


#: Ответ `last` после хода: поздний журнал — второго `env-init`, отказ. В цифрах времени имени
#: журнала стоит «18180» — порт из файла сервера: число из одних цифр не ищется.
LAST = (
    "журнал 20261126T121818018000Z-env-init.log: строк 2, показано 2\n"
    "2026-11-26T12:18:18Z env-init: начало\n"
    "2026-11-26T12:18:18Z итог: отказ — файл окружения уже есть — значения не пересоздаются\n"
    "выката нет\n"
)
#: Файл сервера живого прогона — выдуманные значения; образ ещё пуст (его пишет выкат).
SERVER = {
    "JWT_SECRET": "c" * 64,
    "SITE_DOMAIN": "example.test",
    "WEB_PORT": "18180",
    "BACKEND_IMAGE": "",
}


@pytest.fixture
def last_of(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Callable[..., ModuleType]:
    """Скрипт, у которого `deploy.py last` отвечает заданным кодом, выводом и ошибкой; файл
    сервера — во временном каталоге."""
    live = load()
    monkeypatch.setattr(live, "SERVICE", tmp_path)
    rows = "".join(f"{name}={value}\n" for name, value in SERVER.items())
    (tmp_path / ".env").write_text(rows, encoding="utf-8")

    def answer(code: int, out: str, err: str = "") -> ModuleType:
        done = subprocess.CompletedProcess(["deploy.py", "last"], code, out, err)
        monkeypatch.setattr(live, "deploy", lambda *_args: done)
        return live

    return answer


def test_a183_live_last_shows_the_latest_journal_without_values(last_of) -> None:
    """A183: `last` настоящим процессом после хода — код 0, поздний журнал целиком, «выката нет»,
    ни одного значения файла сервера в выводе."""
    live = last_of(0, LAST)
    assert live.step_last() == "код 0, журнал второго env-init: строк 2, выката нет, значений 0"


@pytest.mark.parametrize(
    ("code", "out", "err", "why"),
    [
        (1, LAST, "", "last — код 1"),
        (0, LAST.replace("-env-init.log", "-put-secret.log"), "", "не поздний журнал"),
        (0, LAST.replace("121818018000Z", "121818Z"), "", "не поздний журнал"),
        (0, LAST.replace("показано 2", "показано 1"), "", "не поздний журнал"),
        (0, "журналов хода нет\nвыката нет\n", "", "не поздний журнал"),
        (0, LAST.replace("выката нет", "идёт выкат: процесс 7 с 2026"), "", "«выката нет»"),
        (0, LAST.replace("начало", "c" * 64), "", "значения в выводе last: JWT_SECRET$"),
        (0, LAST, "домен example.test", "значения в выводе last: SITE_DOMAIN$"),
    ],
    ids=[
        "код",
        "не-поздний",
        "до-секунды",
        "не-целиком",
        "нет-журнала",
        "идёт-выкат",
        "секрет",
        "в-ошибке",
    ],
)
def test_a183_live_last_red(last_of, code: int, out: str, err: str, why: str) -> None:
    """A183: красное шага `last` — код, не поздний журнал или не целиком, нет «выката нет»,
    значение файла сервера в выводе или в ошибке; в причине — имя, не значение."""
    live = last_of(code, out, err)
    with pytest.raises(live.LiveError, match=why):
        live.step_last()
