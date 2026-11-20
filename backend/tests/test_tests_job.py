"""Контракт джобы `tests` (A106–A110): служба базы, покрытие с привязкой, мутации, игнор.

Файл — метка `until` у объявлений гейтов покрытия изменённого и мутаций: с его появлением оба
гейта обязаны стоять в джобе (`scripts/lint/not-applicable.json`, решение 2026-11-19).
"""

from __future__ import annotations

import json
import re
from typing import Any

import envguard
import pytest
import yaml

ROOT = envguard.ROOT
QUALITY = ROOT / ".github" / "workflows" / "quality.yml"
#: Дайджест службы базы — решение 2026-11-20 в decisions.md (сверка «рук», правило 14 суток).
POSTGRES = (
    "postgres:16-alpine@sha256:aa6cb33cf4c19c765cefcf48d96176258800870a9a1e930ad126200f9d825139"
)
IGNORED = (".coverage.*", "coverage.json", "coverage.json.head", "mutants/")


def step(job: dict[str, Any], name: str) -> dict[str, Any]:
    found = [s for s in job.get("steps", []) if str(s.get("name", "")).startswith(name)]
    return found[0] if found else {}


def job_contract_problems(workflow: dict[Any, Any]) -> list[str]:
    """Нарушения контракта джобы `tests` словами: правило и место."""
    job = workflow.get("jobs", {}).get("tests")
    if not job:
        return ["джобы tests нет"]
    service = job.get("services", {}).get("postgres", {})
    run = str(step(job, "Тесты с покрытием").get("run", ""))
    diff = step(job, "Diff coverage").get("env", {})
    mutation = step(job, "Mutation testing")
    checks = [
        (job.get("timeout-minutes") == 25, f"таймаут {job.get('timeout-minutes')!r}, в спеке 25"),
        (
            "uv lock --check" in str(step(job, "Замок свежий").get("run", "")),
            "замок: свежесть не сверяется (`uv sync --frozen` её не видит)",
        ),
        (service.get("image") == POSTGRES, f"служба базы: образ {service.get('image')!r}"),
        (service.get("ports") == ["127.0.0.1:5462:5432"], "служба базы: порт не на петле 5462"),
        (
            "pg_isready -h 127.0.0.1" in str(service.get("options", "")),
            "служба базы: готовность не по TCP",
        ),
        (bool(step(job, "Diff base")), "нет шага Diff base"),
        ("pytest --cov --cov-report=json:coverage.json" in run, "покрытие: не src и migrations"),
        ("git rev-parse HEAD > coverage.json.head" in run, "покрытие: отчёт не привязан"),
        (
            diff.get("LINT_PY_SRC") == "backend",
            f"покрытие изменённого: LINT_PY_SRC={diff.get('LINT_PY_SRC')!r}",
        ),
        (
            diff.get("LINT_COV_FILE") == "backend/coverage.json",
            "покрытие изменённого: не отчёт джобы",
        ),
        (diff.get("MIN_PCT") == "70", f"покрытие изменённого: цель {diff.get('MIN_PCT')!r}"),
        (
            mutation.get("if") == "github.event_name == 'pull_request'",
            "мутации: не только на запросе",
        ),
        (mutation.get("env", {}).get("BUDGET_SEC") == "300", "мутации: бюджет не 300 с"),
        (mutation.get("env", {}).get("MIN_KILLED") == "60", "мутации: цель не 60 %"),
        (mutation.get("env", {}).get("MUTATION_WORKERS") == "1", "мутации: воркеров не один"),
        (
            mutation.get("env", {}).get("LINT_PY_SRC") == "backend/src",
            "мутации: область не backend/src",
        ),
    ]
    return [message for ok, message in checks if not ok]


def test_a106_a107_a108_a109_tests_job_keeps_the_contract() -> None:
    """A99, A106–A109: таймаут по спеке, свежесть замка, служба базы по дайджесту, набор с
    покрытием и привязкой, покрытие изменённого по отчёту джобы, мутации только на запросе в
    бюджете."""
    assert job_contract_problems(yaml.safe_load(QUALITY.read_text(encoding="utf-8"))) == []


def test_a110_reports_and_mutants_are_ignored() -> None:
    """A110: отчёты покрытия и каталог мутантов — в игноре."""
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert [name for name in IGNORED if name not in lines] == []


def test_slice3_gates_are_wired_not_declared() -> None:
    """A108, A109: объявления n/a покрытия изменённого и мутаций сняты — оба гейта в джобе tests."""
    declared = json.loads((ROOT / "scripts" / "lint" / "not-applicable.json").read_text("utf-8"))
    assert {"check_diff_coverage.sh", "check_mutation_gate.sh"} & declared.keys() == set()


def test_a106_conftest_wires_the_run_summary() -> None:
    """A106: строку итога и красный пропуск подключает conftest — на машине и в CI одно правило."""
    conftest = (ROOT / "backend" / "tests" / "conftest.py").read_text(encoding="utf-8")
    assert "from run_summary import" in conftest


COPIES = [
    ("A108-report-not-bound", r"\n\s*git rev-parse HEAD > coverage\.json\.head", "", "не привязан"),
    (
        "A109-mutation-always",
        r"if: github\.event_name == 'pull_request'",
        "if: always()",
        "только на запросе",
    ),
    ("A108-diff-area", r"LINT_PY_SRC: backend\n", "LINT_PY_SRC: backend/src\n", "LINT_PY_SRC"),
    ("A99-timeout-30", r"timeout-minutes: 25\n", "timeout-minutes: 30\n", "в спеке 25"),
    ("lock-not-checked", r"run: uv lock --check\n", "run: uv lock\n", "замок"),
    (
        "A100-service-by-tag",
        r"postgres:16-alpine@sha256:[0-9a-f]{64}",
        "postgres:16-alpine",
        "образ",
    ),
]


@pytest.mark.parametrize(("case", "old", "new", "named"), COPIES, ids=[c[0] for c in COPIES])
def test_tests_job_copies_are_red_and_named(case: str, old: str, new: str, named: str) -> None:
    """A100, A108, A109, копии: порча джобы tests — красный, названо правило."""
    text = QUALITY.read_text(encoding="utf-8")
    spoiled, count = re.subn(old, lambda _: new, text, count=1)
    assert count == 1, f"{case}: в quality.yml нет места порчи {old!r}"
    assert named in "\n".join(job_contract_problems(yaml.safe_load(spoiled)))
