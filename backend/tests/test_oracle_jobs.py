"""Джобы оракула и сканера в CI (A76, A129): файлы workflow и compose, тест без Docker.

Шаг оракула и шаги сканера не глушат свой итог — нет `continue-on-error` и `|| true` (A76, A129).
Строка образа базы одна: служба джобы `tests` и compose стека (A76). Джоба `image-scan` идёт на
запросе и на push в main, токен — только чтение (A129). Права у джоб `quality.yml` — сверху: свой
блок `permissions` джобе запрещает тест контракта В1а (A100), поэтому судятся действующие права.
"""

from __future__ import annotations

import re
from typing import Any

import envguard
import pytest
import yaml

ROOT = envguard.ROOT
QUALITY = ROOT / ".github" / "workflows" / "quality.yml"
COMPOSE = ROOT / "docker-compose.yml"
#: Мягкий выход шага: `|| true`, `|| :` и `|| exit 0` — итог команды теряется.
SOFT_EXIT = re.compile(r"\|\|\s*(?:true|:|exit\s+0)(?![\w-])")
SCAN_COMMANDS = ("install", "run", "sbom")


def load(path: Any) -> dict[Any, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path.name}: не словарь YAML"
    return data


def soft_exit(job_name: str, job: dict[str, Any], marker: str) -> list[str]:
    """Шаги с `marker` в команде: есть хотя бы один, и ни один не глушит свой итог."""
    found = [s for s in job.get("steps", []) if marker in str(s.get("run", ""))]
    problems = [] if found else [f"{job_name}: нет шага с `{marker}`"]
    if job.get("continue-on-error"):
        problems.append(f"{job_name}: continue-on-error у джобы")
    if "if" in job:
        problems.append(f"{job_name}: условие у джобы — {job['if']}")
    for step in found:
        name = step.get("name", marker)
        if step.get("continue-on-error"):
            problems.append(f"{job_name}: шаг «{name}» — continue-on-error")
        if SOFT_EXIT.search(str(step.get("run", ""))):
            problems.append(f"{job_name}: шаг «{name}» — мягкий выход")
        if "if" in step:
            problems.append(f"{job_name}: шаг «{name}» — условие {step['if']}")
    return problems


def trigger_problems(workflow: dict[Any, Any], job_name: str) -> list[str]:
    """Workflow идёт на запросе в main и на push в main: фильтр веток main не исключает."""
    triggers = workflow.get(True) or workflow.get("on") or {}
    request = triggers.get("pull_request", False)
    push = (triggers.get("push") or {}).get("branches", [])
    problems = []
    if request is False or "main" not in push:
        problems.append(f"{job_name}: workflow идёт не на запросе и не на push в main")
    request = request or {}
    if "main" not in request.get("branches", ["main"]) or "main" in request.get(
        "branches-ignore", []
    ):
        problems.append(f"{job_name}: запрос в main джобу не запустит — фильтр веток")
    return problems


def stack_problems(workflow: dict[Any, Any]) -> list[str]:
    """A76: джоба `stack` гоняет оракул на запросе, и её итог — итог оракула."""
    job = workflow.get("jobs", {}).get("stack")
    if not job:
        return ["джобы stack нет"]
    return trigger_problems(workflow, "stack") + soft_exit("stack", job, "scripts/stack_oracle.py")


def scan_problems(workflow: dict[Any, Any]) -> list[str]:
    """A129: джоба `image-scan` — на запросе и на push в main, права на чтение, шаги не глушат."""
    job = workflow.get("jobs", {}).get("image-scan")
    if not job:
        return ["джобы image-scan нет"]
    problems = trigger_problems(workflow, "image-scan")
    rights = job.get("permissions", workflow.get("permissions"))
    if rights != {"contents": "read"}:
        problems.append(f"image-scan: права {rights!r}, а не contents: read")
    for command in SCAN_COMMANDS:
        problems += soft_exit("image-scan", job, f"scripts/image_scan.py {command}")
    return list(dict.fromkeys(problems))


def db_image_problems(workflow: dict[Any, Any], compose: dict[Any, Any]) -> list[str]:
    """A76: строка образа базы — одна в службе джобы `tests` и в compose стека, с дайджестом."""
    places = {
        ".github/workflows/quality.yml": (
            workflow.get("jobs", {}).get("tests", {}).get("services", {}).get("postgres", {})
        ),
        "docker-compose.yml": compose.get("services", {}).get("db", {}),
    }
    images = {name: str(node.get("image", "")) for name, node in places.items()}
    problems = [f"образа базы нет в {name}" for name, image in images.items() if not image]
    problems += [
        f"образ базы без дайджеста в {name}"
        for name, image in images.items()
        if image and "@sha256:" not in image
    ]
    if not problems and len(set(images.values())) > 1:
        problems.append(
            "образ базы расходится: " + "; ".join(f"{n} — {i}" for n, i in images.items())
        )
    return problems


def test_a76_stack_job_result_is_the_oracle_result() -> None:
    """A76, четвёртая строка: у шага оракула в джобе `stack` нет `continue-on-error` и `|| true`."""
    assert stack_problems(load(QUALITY)) == []


def test_a129_image_scan_job_runs_on_request_and_main_with_read_token() -> None:
    """A129: джоба `image-scan` — на запросе и на push в main, токен на чтение; шаги установки,
    скана и описи не глушат свой итог."""
    assert scan_problems(load(QUALITY)) == []


def test_a76_db_image_line_is_one_for_tests_job_and_stack() -> None:
    """A76, пятая строка: строка образа базы в службе джобы `tests` и в compose стека одна."""
    assert db_image_problems(load(QUALITY), load(COMPOSE)) == []


def step_with(job: dict[str, Any], marker: str) -> dict[str, Any]:
    return next(s for s in job["steps"] if marker in str(s.get("run", "")))


def spoil_stack(workflow: dict[Any, Any], case: str) -> str:
    """Порча джобы `stack`; возвращает, что обязано быть названо."""
    job = workflow["jobs"]["stack"]
    step = step_with(job, "scripts/stack_oracle.py")
    tails = {"or-true": " || true", "or-colon": " || :", "or-exit-0": " || exit 0"}
    if case == "continue":
        step["continue-on-error"] = True
    elif case in tails:
        step["run"] += tails[case]
    elif case == "step-if":
        step["if"] = "false"
    elif case == "job-if":
        job["if"] = "false"
        return "условие у джобы"
    else:
        (workflow.get(True) or workflow["on"])["pull_request"] = {"branches": ["release"]}
        return "фильтр веток"
    return str(step["name"])


STACK_COPIES = ["continue", "or-true", "or-colon", "or-exit-0", "step-if", "job-if", "pr-branches"]


@pytest.mark.parametrize("case", STACK_COPIES)
def test_a76_stack_copies_are_red_and_named(case: str) -> None:
    """A76, четвёртая строка, копии: «continue-on-error: true», мягкий выход (`|| true`, `|| :`,
    `|| exit 0`), условие у шага или джобы, запрос с фильтром веток без main — красное с именем
    шага или правила."""
    workflow = load(QUALITY)
    named = spoil_stack(workflow, case)
    assert any(named in p for p in stack_problems(workflow))


SCAN_COPIES = [
    "no-push",
    "job-if",
    "write-token",
    "step-if-run",
    "or-exit-0-sbom",
    "pr-ignore-main",
    *(f"soft-{c}" for c in SCAN_COMMANDS),
]


@pytest.mark.parametrize("case", SCAN_COPIES)
def test_a129_scan_copies_are_red_and_named(case: str) -> None:
    """A129, копии: без push в main, условие у джобы или шага, токен на запись, мягкий выход у
    шага сканера, запрос без main в фильтре веток — красное с именем правила или шага."""
    workflow = load(QUALITY)
    job = workflow["jobs"]["image-scan"]
    if case == "no-push":
        (workflow.get(True) or workflow["on"]).pop("push")
        named = "push в main"
    elif case == "job-if":
        job["if"] = "github.event_name == 'pull_request'"
        named = "условие"
    elif case == "write-token":
        job["permissions"] = {"contents": "write"}
        named = "права"
    elif case == "step-if-run":
        step = step_with(job, "scripts/image_scan.py run")
        step["if"] = "false"
        named = str(step["name"])
    elif case == "or-exit-0-sbom":
        step = step_with(job, "scripts/image_scan.py sbom")
        step["run"] += " || exit 0"
        named = str(step["name"])
    elif case == "pr-ignore-main":
        (workflow.get(True) or workflow["on"])["pull_request"] = {"branches-ignore": ["main"]}
        named = "фильтр веток"
    else:
        step = step_with(job, f"scripts/image_scan.py {case.removeprefix('soft-')}")
        step["run"] = f"{step['run']} || true"
        named = step["name"]
    assert any(named in p for p in scan_problems(workflow))


@pytest.mark.parametrize("case", ["digest", "tests-missing", "compose-missing"])
def test_a76_db_image_copies_are_red_and_name_both_places(case: str) -> None:
    """A76, пятая строка, копии: расхождение — красное с именами двух файлов; образа нет в одном
    из мест — красное с именем места, а не «сверять не с чем»."""
    workflow, compose = load(QUALITY), load(COMPOSE)
    service = workflow["jobs"]["tests"]["services"]["postgres"]
    if case == "digest":
        compose["services"]["db"]["image"] = "postgres:16-alpine@sha256:" + "0" * 64
        expected = [".github/workflows/quality.yml", "docker-compose.yml"]
    elif case == "tests-missing":
        service.pop("image")
        expected = [".github/workflows/quality.yml"]
    else:
        compose["services"]["db"].pop("image")
        expected = ["docker-compose.yml"]
    found = "\n".join(db_image_problems(workflow, compose))
    assert all(name in found for name in expected), found
