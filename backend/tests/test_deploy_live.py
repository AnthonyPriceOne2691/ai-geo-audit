"""Живые `env-init` и `put-secret` (6а-2, срез 4; A182, A183): скрипт шага джобы `stack` грузится по
пути. Без машины CI и без root — отказ словами до любой записи; файл владельца — ровно имена
«задаёт владелец» блока «сервер»; шаг стоит в джобе после оракула. Сам прогон — только в CI."""

from __future__ import annotations

import importlib.util
import os
import re
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
def no_copy(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Скрипт, у которого копия клона — красное: отказ обязан прийти раньше записи."""
    live = load()

    def copy(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("запись до проверки машины")

    monkeypatch.setattr(live.shutil, "copytree", copy)
    return live


def test_a182_live_run_refuses_outside_ci(
    no_copy: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A182: не на машине джобы CI — отказ с кодом 2 и словами: скрипт пишет в `/srv` и `/etc`;
    на диске ничего не появилось — ни каталога сервиса, ни своего `/etc`."""
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    roots = (no_copy.SERVICE, no_copy.ETC)
    before = [os.path.lexists(root) for root in roots]
    assert no_copy.main() == 2
    assert "отказ: только на машине джобы CI" in capsys.readouterr().err
    assert [os.path.lexists(root) for root in roots] == before


def test_a182_live_run_refuses_without_root(
    no_copy: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A182: на машине CI, но не от root — отказ с кодом 2: `env-init` ставит владельцев файлов."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(os, "geteuid", lambda: 1001)
    assert no_copy.main() == 2
    assert "отказ: нужен root — шаг джобы под sudo" in capsys.readouterr().err


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
