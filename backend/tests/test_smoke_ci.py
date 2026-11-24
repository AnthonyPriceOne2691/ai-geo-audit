"""Контракт CI сквозных проверок smoke (A372, A376): S2 — команда шага «Тесты с покрытием» без
`--cov…`; шаг smoke в `tests` после набора — команда целиком, без глушилок и конвейера; шаг шаблона
«Smoke evals» в `delivery` `run.sh` не гоняет. Помощник и прогон — в `test_smoke_run.py`."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import envguard
import pytest
import smoke_run
import yaml
from test_oracle_jobs import soft_exit

QUALITY = envguard.ROOT / ".github" / "workflows" / "quality.yml"
MARKER = "delivery/evals/smoke/run.sh"
SMOKE_RUN = f"bash {MARKER} --skip 'S2=набор прошёл шагом «Тесты с покрытием» этой джобы'"
TEMPLATE_STEP = "Smoke evals (product oracles)"


def test_a372_s2_is_the_ci_test_step_without_coverage() -> None:
    """A372: S2 — команда шага «Тесты с покрытием» из `backend/` без `--cov…`."""
    jobs = yaml.safe_load(QUALITY.read_text(encoding="utf-8"))["jobs"]
    step = next(s for s in jobs["tests"]["steps"] if s.get("name") == "Тесты с покрытием")
    words = str(step["run"]).splitlines()[0].split()
    found = (step["working-directory"], [w for w in words if not w.startswith("--cov")])
    assert found == ("backend", list(smoke_run.S2_COMMAND))


def smoke_ci_problems(workflow: dict[Any, Any]) -> list[str]:
    """A376: шаг smoke в `tests` после набора — команда целиком, без глушилок и конвейера
    (`soft_exit`, L91); шаг шаблона «Smoke evals» в `delivery` run.sh не гоняет: там нет ни базы,
    ни зависимостей."""
    jobs = workflow.get("jobs", {})
    tests = jobs.get("tests", {})
    steps = tests.get("steps", [])
    names = [str(s.get("name", "")) for s in steps]
    smoke = [i for i, s in enumerate(steps) if MARKER in str(s.get("run", ""))]
    problems = soft_exit("tests", tests, MARKER)
    if smoke and str(steps[smoke[0]].get("run", "")).strip() != SMOKE_RUN:
        problems.append("tests: команда шага smoke — не целиком")
    if smoke and "Тесты с покрытием" in names and smoke[0] < names.index("Тесты с покрытием"):
        problems.append("tests: шаг smoke раньше набора")
    template = [
        s for s in jobs.get("delivery", {}).get("steps", []) if s.get("name") == TEMPLATE_STEP
    ]
    if not template or f"bash {MARKER}" in str(template[0].get("run", "")):
        problems.append("delivery: шаг «Smoke evals» гоняет run.sh без базы и зависимостей")
    return problems


def test_a376_smoke_runs_in_the_tests_job_after_the_suite() -> None:
    """A376: в `tests` после набора — `run.sh --skip 'S2=…'` (S3 и S1, пропуск S2 назван в итоге);
    команда шага — целиком по контракту, глушилок и конвейера нет; в `delivery` — строка, где идёт
    smoke."""
    assert smoke_ci_problems(yaml.safe_load(QUALITY.read_text(encoding="utf-8"))) == []


def smoke_step(workflow: dict[Any, Any]) -> dict[str, Any]:
    return next(s for s in workflow["jobs"]["tests"]["steps"] if MARKER in str(s.get("run", "")))


def before_suite(workflow: dict[Any, Any]) -> None:
    steps = workflow["jobs"]["tests"]["steps"]
    step = smoke_step(workflow)
    steps.remove(step)
    steps.insert(0, step)


def template_runs_it(workflow: dict[Any, Any]) -> None:
    steps = workflow["jobs"]["delivery"]["steps"]
    next(s for s in steps if s.get("name") == TEMPLATE_STEP)["run"] = f"bash {MARKER}"


COPIES: dict[str, tuple[Callable[[dict[Any, Any]], None], str]] = {
    "нет шага": (lambda w: w["jobs"]["tests"]["steps"].remove(smoke_step(w)), "нет шага с"),
    "мягкий выход": (lambda w: smoke_step(w).update(run=f"{SMOKE_RUN} || true"), "мягкий выход"),
    "continue-on-error": (
        lambda w: smoke_step(w).update({"continue-on-error": True}),
        "continue-on-error",
    ),
    "условие шага": (lambda w: smoke_step(w).update({"if": "always()"}), "условие"),
    "конвейер": (lambda w: smoke_step(w).update(run=f"{SMOKE_RUN} | tee smoke.log"), "не целиком"),
    "пропуск S1": (
        lambda w: smoke_step(w).update(run=SMOKE_RUN.replace("'S2=", "'S1=")),
        "не целиком",
    ),
    "раньше набора": (before_suite, "раньше набора"),
    "delivery гоняет run.sh": (template_runs_it, "delivery:"),
}


@pytest.mark.parametrize("case", list(COPIES))
def test_a376_smoke_step_copies_are_red_and_named(case: str) -> None:
    """A376, копии: без шага, с глушилкой, условием, конвейером, другой командой, раньше набора и
    с `delivery`, гоняющей run.sh, — красные, каждая своей строкой."""
    change, named = COPIES[case]
    workflow = yaml.safe_load(QUALITY.read_text(encoding="utf-8"))
    change(workflow)
    problems = smoke_ci_problems(workflow)
    assert [p for p in problems if named in p], problems
