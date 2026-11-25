"""Контракт джобы `tests` (A106–A110): служба базы, покрытие с привязкой, игнор; мутации — доли
`mutation-shard` и джоба суммы `mutation` (A289).

Файл — метка `until` у объявлений гейтов покрытия изменённого и мутаций: с его появлением оба
гейта обязаны стоять в джобе (`scripts/lint/not-applicable.json`, решение 2026-11-19).
"""

from __future__ import annotations

import json
import re
from typing import Any

import envguard
import pytest
import redistools
import yaml

ROOT = envguard.ROOT
QUALITY = ROOT / ".github" / "workflows" / "quality.yml"
#: Дайджест службы базы — решение 2026-11-20 в decisions.md (сверка «рук», правило 14 суток).
POSTGRES = (
    "postgres:16-alpine@sha256:aa6cb33cf4c19c765cefcf48d96176258800870a9a1e930ad126200f9d825139"
)
IGNORED = (".coverage.*", "coverage.json", "coverage.json.head", "mutants/")
#: Redis тестов в CI — как Redis стенда: строка образа стека, пароль стенда, петля 6462.
REDIS_READY = "redis-cli -h 127.0.0.1 -e ping"


def stack_redis_image() -> str:
    """Строка образа redis стека «линия@дайджест» — её же берут стенд и служба CI (A100)."""
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    return str(compose["services"]["redis"]["image"])


def step(job: dict[str, Any], name: str) -> dict[str, Any]:
    found = [s for s in job.get("steps", []) if str(s.get("name", "")).startswith(name)]
    return found[0] if found else {}


def job_contract_problems(workflow: dict[Any, Any]) -> list[str]:
    """Нарушения контракта джобы `tests` словами: правило и место."""
    job = workflow.get("jobs", {}).get("tests")
    if not job:
        return ["джобы tests нет"]
    service = job.get("services", {}).get("postgres", {})
    redis = job.get("services", {}).get("redis", {})
    run = str(step(job, "Тесты с покрытием").get("run", ""))
    diff = step(job, "Diff coverage").get("env", {})
    checks = [
        (job.get("timeout-minutes") == 15, f"таймаут {job.get('timeout-minutes')!r}, ждём 15"),
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
        (redis.get("image") == stack_redis_image(), f"служба Redis: образ {redis.get('image')!r}"),
        (redis.get("ports") == ["127.0.0.1:6462:6379"], "служба Redis: порт не на петле 6462"),
        (
            redis.get("command") == f"redis-server --requirepass {redistools.PASSWORD}",
            "служба Redis: пароль стенда не в command",
        ),
        (
            (redis.get("env") or {}).get("REDISCLI_AUTH") == redistools.PASSWORD,
            "служба Redis: готовность без пароля в REDISCLI_AUTH",
        ),
        (
            REDIS_READY in str(redis.get("options", "")),
            "служба Redis: готовность не `redis-cli -e` — без -e ответ-ошибка даёт код 0 (L60)",
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
        (not step(job, "Mutation testing"), "мутации: шаг остался в tests, а не в долях (A289)"),
    ]
    return [message for ok, message in checks if not ok] + mutation_jobs_problems(workflow)


#: Обязательные проверки main после хода 3 — слово владельца 2026-11-23T11:29Z (A116, A289) —
#: и доли, сведённые суммой; других джоб нет.
JOBS = {"gates", "delivery", "tests", "mutation-shard", "mutation", "stack", "image-scan"}


def mutation_jobs_problems(workflow: dict[Any, Any]) -> list[str]:
    """A289: порчи — matrix из 4 долей параллельно с `tests`, порог — джоба суммы при любом исходе
    долей; счёт доли — артефактом; служба базы долей — как у `tests`."""
    jobs = workflow.get("jobs", {})
    shard, total = jobs.get("mutation-shard", {}), jobs.get("mutation", {})
    gate = step(shard, "Mutation testing (доля)").get("env", {})
    summed = step(total, "Mutation testing (сумма").get("env", {})
    upload = next(
        (s for s in shard.get("steps", []) if "upload-artifact" in str(s.get("uses"))), {}
    )
    download = next(
        (s for s in total.get("steps", []) if "download-artifact" in str(s.get("uses"))), {}
    )
    checks = [
        (set(jobs) == JOBS, f"джобы {sorted(jobs)}, ждём {sorted(JOBS)}"),
        (shard.get("if") == "github.event_name == 'pull_request'", "доли: не только на запросе"),
        (shard.get("strategy", {}).get("matrix", {}).get("shard") == [1, 2, 3, 4], "долей не 4"),
        (shard.get("strategy", {}).get("fail-fast") is False, "доли: fail-fast снимет соседей"),
        (gate.get("MUTATION_SHARD") == "${{ matrix.shard }}/4", "доли: MUTATION_SHARD не k/4"),
        (gate.get("BUDGET_SEC") == "1200", "доли: бюджет не 1200 с"),
        ("MIN_KILLED" not in gate, "доли: порог у доли — доля только считает, судит сумма"),
        (gate.get("MUTATION_WORKERS") == "4", "доли: воркеров не 4"),
        (gate.get("LINT_PY_SRC") == "backend/src", "доли: область не backend/src"),
        (
            shard.get("services") == jobs.get("tests", {}).get("services"),
            "доли: службы базы и Redis не как у tests",
        ),
        (
            upload.get("with", {}).get("name") == "mutation-shard-${{ matrix.shard }}",
            "доли: имя счёта",
        ),
        (upload.get("with", {}).get("path") == "reports/mutation/shards/", "доли: путь счёта"),
        (total.get("needs") == "mutation-shard", "сумма: needs не на доли"),
        ("always()" in str(total.get("if")), "сумма: без `if: always()` упавшая доля её пропустит"),
        ("pull_request" in str(total.get("if")), "сумма: не только на запросе"),
        (
            download.get("with", {}).get("pattern") == "mutation-shard-*",
            "сумма: не все счета долей",
        ),
        (
            download.get("with", {}).get("merge-multiple") is True,
            "сумма: счета не в одном каталоге",
        ),
        (summed.get("MUTATION_SUM") == "4", "сумма: не 4"),
        (summed.get("MIN_KILLED") == "60", "сумма: порог не 60 % — он судит здесь (A109)"),
        (set(summed) == {"MUTATION_SUM", "MIN_KILLED"}, f"сумма: лишнее в env {sorted(summed)}"),
    ]
    return [message for ok, message in checks if not ok]


def test_a106_a107_a108_a109_tests_job_keeps_the_contract() -> None:
    """A99, A106–A109: таймаут по спеке, свежесть замка, службы базы и Redis по дайджесту, набор с
    покрытием и привязкой, покрытие изменённого по отчёту джобы; мутации — 4 доли только на запросе
    в бюджете и сумма при любом исходе долей (A289)."""
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
        r"if: github\.event_name == 'pull_request'\n    runs-on",
        "if: always()\n    runs-on",
        "доли: не только на запросе",
    ),
    ("A108-diff-area", r"LINT_PY_SRC: backend\n", "LINT_PY_SRC: backend/src\n", "LINT_PY_SRC"),
    ("A99-timeout-25", r"timeout-minutes: 15\n", "timeout-minutes: 25\n", "ждём 15"),
    ("lock-not-checked", r"run: uv lock --check\n", "run: uv lock\n", "замок"),
    ("A281-workers-1", r'MUTATION_WORKERS: "4"', 'MUTATION_WORKERS: "1"', "воркеров не 4"),
    ("A289-sum-not-always", r"if: always\(\) && ", "if: ", "без `if: always()`"),
    ("A289-sum-no-needs", r" {4}needs: mutation-shard\n", "", "needs не на доли"),
    ("A289-three-shards", r"shard: \[1, 2, 3, 4\]", "shard: [1, 2, 3]", "долей не 4"),
    ("A289-fail-fast", r"fail-fast: false", "fail-fast: true", "fail-fast"),
    ("A289-shard-of-3", r"\{\{ matrix\.shard \}\}/4", "{{ matrix.shard }}/3", "не k/4"),
    ("A289-sum-of-3", r'MUTATION_SUM: "4"', 'MUTATION_SUM: "3"', "сумма: не 4"),
    ("A289-sum-threshold-50", r'MIN_KILLED: "60"', 'MIN_KILLED: "50"', "сумма: порог не 60 %"),
    (
        "A289-threshold-on-shard",
        r'(?=          BUDGET_SEC: "1200")',
        '          MIN_KILLED: "60"\n',
        "порог у доли",
    ),
    ("A289-one-count", r"pattern: mutation-shard-\*", "pattern: mutation-shard-1", "все счета"),
    (
        "A289-db-by-tag",
        r"\(A289\)\.\n      postgres:\n        image: postgres:16-alpine@sha256:[0-9a-f]{64}",
        "(A289).\n      postgres:\n        image: postgres:16-alpine",
        "не как у tests",
    ),
    (
        "A289-redis-by-tag",
        r"\(A289\)\.\n      redis:\n        image: redis:7\.4-alpine@sha256:[0-9a-f]{64}",
        "(A289).\n      redis:\n        image: redis:7.4-alpine",
        "не как у tests",
    ),
    (
        "A100-redis-by-tag",
        r"redis:7\.4-alpine@sha256:[0-9a-f]{64}",
        "redis:7.4-alpine",
        "Redis: образ",
    ),
    (
        "redis-no-password",
        r"command: redis-server --requirepass \S+",
        "command: redis-server",
        "command",
    ),
    (
        "redis-ready-without-e",
        r"redis-cli -h 127\.0\.0\.1 -e ping",
        "redis-cli -h 127.0.0.1 ping",
        "-e",
    ),
    ("redis-ready-no-auth", r"\n {10}REDISCLI_AUTH: \S+", "", "REDISCLI_AUTH"),
    (
        "A289-step-back-in-tests",
        r"(?=\n  # Ход 3 «Скорости CI»)",
        "\n      - name: Mutation testing (changed files, budgeted)\n"
        "        run: bash scripts/lint/check_mutation_gate.sh\n",
        "шаг остался в tests",
    ),
    (
        "A100-service-by-tag",
        r"postgres:16-alpine@sha256:[0-9a-f]{64}",
        "postgres:16-alpine",
        "образ",
    ),
]


@pytest.mark.parametrize(("case", "old", "new", "named"), COPIES, ids=[c[0] for c in COPIES])
def test_tests_job_copies_are_red_and_named(case: str, old: str, new: str, named: str) -> None:
    """A100, A108, A109, A281, A289, копии: порча джобы tests, долей или суммы — красный, названо
    правило."""
    text = QUALITY.read_text(encoding="utf-8")
    spoiled, count = re.subn(old, lambda _: new, text, count=1)
    assert count == 1, f"{case}: в quality.yml нет места порчи {old!r}"
    assert named in "\n".join(job_contract_problems(yaml.safe_load(spoiled)))
