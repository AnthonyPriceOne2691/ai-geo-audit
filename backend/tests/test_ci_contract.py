"""Контракт CI (A99–A101, A103, A104, A117, A121, A123): workflow, хуки, STATUS и замок uv.

Тесты читают файлы дерева и в сеть не ходят. Каждое правило — функция, которая возвращает
список нарушений словами: копия-порча называет правило и место, а не падает молча.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import tomllib
from datetime import datetime
from pathlib import Path
from typing import Any

import envguard
import pytest
import yaml

ROOT = envguard.ROOT
QUALITY = ROOT / ".github" / "workflows" / "quality.yml"
GUARD = ROOT / ".github" / "workflows" / "main-guard.yml"
HOOKS = ROOT / ".pre-commit-config.yaml"
PYPROJECT = ROOT / "backend" / "pyproject.toml"
UV_LOCK = ROOT / "backend" / "uv.lock"
STATUS = ROOT / "delivery" / "active" / "STATUS.md"
#: Пакеты моложе отсечки, которые уже стояли в замке до правила (решение проверяющего 19.11).
LOCK_EXCEPTIONS = {"sqlalchemy", "pydantic-settings", "pytest-asyncio"}
#: Шаг аудита смотрит JS-половину в несуществующий каталог: npm в CI — поставка В1б.
AUDIT_FE_DIR = ".frontend-not-here"
PINNED = re.compile(r"^\s*-?\s*uses:\s*([\w.-]+/[\w./-]+)@([0-9a-f]{40})\s+#\s*v\d+\.\d+\.\d+\s*$")
EXPORT = re.compile(r'export (LINT_[A-Z_]+)="([^"]*)"')
REQUEST_FIELDS = re.compile(r"\$\{\{\s*github\.(head_ref|event\.pull_request\.)")


def load(path: Path) -> dict[Any, Any]:
    """A99, A100: файл workflow или конфига хуков — словарь YAML."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path.name}: не словарь YAML"
    return data


def code_lines(text: str) -> list[tuple[int, str]]:
    """Строки без комментариев: правило судит то, что исполняется, а не пояснение."""
    out = []
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.split(" #", 1)[0] if not line.lstrip().startswith("#") else ""
        if stripped.strip():
            out.append((number, stripped))
    return out


def steps(workflow: dict[Any, Any]) -> list[tuple[str, dict[Any, Any], dict[Any, Any]]]:
    return [
        (name, job, step) for name, job in workflow["jobs"].items() for step in job.get("steps", [])
    ]


def hook_exports() -> dict[str, dict[str, str]]:
    """Скрипт гейта → `LINT_*`, которые экспортирует его хук."""
    out: dict[str, dict[str, str]] = {}
    for repo in load(HOOKS)["repos"]:
        for hook in repo["hooks"]:
            entry = hook.get("entry", "")
            for script in re.findall(r"scripts/lint/([\w.-]+)", entry):
                out.setdefault(script, {}).update(EXPORT.findall(entry))
    return out


def lint_env_problems(workflow: dict[Any, Any], exports: dict[str, dict[str, str]]) -> list[str]:
    """A99: у шага с гейтом `LINT_*` те же, что `export` его хука; исключение — шаг аудита."""
    problems = []
    for job_name, job, step in steps(workflow):
        env = {**workflow.get("env", {}), **job.get("env", {}), **step.get("env", {})}
        for script in re.findall(r"scripts/lint/([\w.-]+)", str(step.get("run", ""))):
            if script == "check_deps_audit.sh" and env.get("LINT_FE_DIR") != AUDIT_FE_DIR:
                problems.append(f"{job_name}: шаг аудита — LINT_FE_DIR не {AUDIT_FE_DIR}")
            for var, value in exports.get(script, {}).items():
                if str(env.get(var)) != value:
                    problems.append(
                        f"{job_name}/{script}: {var}={env.get(var)!r}, у хука {value!r}"
                    )
    return problems


def strict_problems(text: str, workflow: dict[Any, Any]) -> list[str]:
    """A99, A103: режим строгий везде, у каждой джобы предохранитель по времени."""
    problems = []
    if workflow.get("env", {}).get("STRICT") != "1":
        problems.append('STRICT: сверху не "1"')
    for number, line in code_lines(text):
        if re.search(r"STRICT\s*[:=]\s*['\"]?0", line):
            problems.append(f"STRICT: строка {number} включает мягкий режим")
    for name, job in workflow["jobs"].items():
        if not isinstance(job.get("timeout-minutes"), int):
            problems.append(f"timeout-minutes: у джобы {name} нет предохранителя")
    return problems


def install_problems(text: str, uv_version: str) -> list[str]:
    """A99, A123: инструменты — из замка; `pip install` — только сам uv нужной версии."""
    problems = []
    runs = [line for _, line in code_lines(text)]
    if not any(re.search(r"\buv sync --frozen\b", line) for line in runs):
        problems.append("установка: нет `uv sync --frozen`")
    for line in runs:
        for found in re.findall(r"pip install ([^\s|;&]+(?: [^\s|;&]+)*)", line):
            if found != f"uv=={uv_version}":
                problems.append(
                    f"установка: `pip install {found}`, а разрешён только uv=={uv_version}"
                )
        if re.search(r"\buv sync\b(?! --frozen)", line):
            problems.append(f"установка: `uv sync` без --frozen — {line.strip()}")
    return problems


def uses_problems(text: str) -> list[str]:
    """A100: действие — по полному хешу коммита, версия — в комментарии строки."""
    return [
        f"не закреплено по коммиту: строка {number}: {line.strip()}"
        for number, line in enumerate(text.splitlines(), 1)
        if re.match(r"^\s*-?\s*uses:", line) and not PINNED.match(line)
    ]


def token_problems(text: str, workflow: dict[Any, Any], writers: dict[str, str]) -> list[str]:
    """A100: сверху только чтение, запись — у названных джоб; без чужих триггеров и секретов."""
    problems = []
    if workflow.get("permissions") != {"contents": "read"}:
        problems.append(
            f"права токена: сверху {workflow.get('permissions')!r}, а не contents: read"
        )
    for name, job in workflow["jobs"].items():
        granted = job.get("permissions", {})
        want = {writers[name]: "write"} if name in writers else {}
        if granted != want:
            problems.append(f"права токена: у джобы {name} {granted!r}, ожидалось {want!r}")
    if "pull_request_target" in (workflow.get(True) or workflow.get("on") or {}):
        problems.append("триггер: pull_request_target запрещён")
    for _, _, step in steps(workflow):
        checkout = "actions/checkout" in str(step.get("uses", ""))
        if checkout and step.get("with", {}).get("persist-credentials") is not False:
            problems.append("checkout: токен сохраняется — нужен persist-credentials: false")
        if REQUEST_FIELDS.search(str(step.get("run", ""))):
            problems.append(f"run: поле запроса в команде — {step.get('name', '')}")
    for number, line in code_lines(text):
        if "secrets." in line:
            problems.append(f"секреты: строка {number} читает secrets.")
    for name, job in workflow["jobs"].items():
        for service in job.get("services", {}).values():
            if "@sha256:" not in str(service.get("image", "")):
                problems.append(f"служба джобы {name}: образ не по дайджесту")
    return problems


def concurrency_problems(workflow: dict[Any, Any]) -> list[str]:
    """A101: отменяется только устаревший прогон запроса; у main группа — по хешу коммита."""
    group = str(workflow.get("concurrency", {}).get("group", ""))
    cancel = workflow.get("concurrency", {}).get("cancel-in-progress")
    problems = []
    if "github.sha" not in group:
        problems.append(f"concurrency: группа main без хеша коммита — {group!r}")
    if cancel is True or "github.event_name == 'pull_request'" not in str(cancel):
        problems.append(f"concurrency: cancel-in-progress={cancel!r} — отмена не только у запроса")
    return problems


def guard_problems(guard: dict[Any, Any]) -> list[str]:
    """A117: тревога — по красному прогону quality на main и только от push."""
    trigger = (guard.get(True) or guard.get("on") or {}).get("workflow_run", {})
    alert = guard["jobs"].get("alert", {})
    condition = str(alert.get("if", ""))
    problems = []
    if trigger.get("workflows") != ["quality"] or trigger.get("branches") != ["main"]:
        problems.append(f"main-guard: workflow_run не по quality на main — {trigger!r}")
    for part in ("conclusion == 'failure'", "event == 'push'"):
        if part not in condition:
            problems.append(f"main-guard: в условии alert нет {part}")
    return problems


def python_minor() -> str:
    """A99: минор Python — нижняя граница `requires-python`."""
    spec = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]["requires-python"]
    found = re.search(r">=\s*(\d+\.\d+)", spec)
    assert found, f"requires-python без нижней границы: {spec}"
    return found.group(1)


def lock_problems(lock: dict[str, Any]) -> list[str]:
    """A121: в замке нет файла новее отсечки, кроме поимённых исключений со своей датой."""
    options = lock.get("options", {})
    cutoff = datetime.fromisoformat(options["exclude-newer"])
    own = {
        name: datetime.fromisoformat(at)
        for name, at in options.get("exclude-newer-package", {}).items()
    }
    problems = (
        [f"исключения замка: {sorted(own)} вместо {sorted(LOCK_EXCEPTIONS)}"]
        if set(own) != LOCK_EXCEPTIONS
        else []
    )
    for package in lock["package"]:
        limit = own.get(package["name"], cutoff)
        for item in [package.get("sdist", {}), *package.get("wheels", [])]:
            uploaded = item.get("upload-time")
            if uploaded and datetime.fromisoformat(uploaded) > limit:
                problems.append(
                    f"замок: {package['name']} {package['version']} загружен {uploaded}"
                )
                break
    return problems


def uv_version() -> str:
    required = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["uv"][
        "required-version"
    ]
    return str(required).removeprefix("==")


def quality_check(text: str) -> list[str]:
    workflow = yaml.safe_load(text)
    return [
        *uses_problems(text),
        *token_problems(text, workflow, {}),
        *concurrency_problems(workflow),
        *strict_problems(text, workflow),
        *install_problems(text, uv_version()),
        *lint_env_problems(workflow, hook_exports()),
    ]


def guard_check(text: str) -> list[str]:
    workflow = yaml.safe_load(text)
    return [
        *uses_problems(text),
        *token_problems(text, workflow, {"alert": "issues"}),
        *guard_problems(workflow),
    ]


def status_check(text: str) -> list[str]:
    """A104: строка `new_dependency:` — только решением человека."""
    lines = [line for line in text.splitlines() if "**new_dependency:**" in line]
    return [
        f"new_dependency без by=human: — {line.strip()}"
        for line in lines
        if "by=human:" not in line
    ]


def hooks_check(text: str) -> list[str]:
    """A100: в конфиге хуков только `repo: local`."""
    return [
        f"конфиг хуков: внешний repo {repo['repo']}"
        for repo in yaml.safe_load(text)["repos"]
        if repo["repo"] != "local"
    ]


def test_a99_python_in_ci_is_the_minor_of_requires_python() -> None:
    """A99: Python в CI — минор из `requires-python`."""
    versions = {
        str(step.get("with", {}).get("python-version"))
        for _, _, step in steps(load(QUALITY))
        if "actions/setup-python" in str(step.get("uses", ""))
    }
    assert versions == {python_minor()}


def test_a99_coverage_measures_src_and_migrations_in_subprocesses() -> None:
    """A99: покрытие — `src` и `migrations`, подпроцессы — через `patch`."""
    run = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["coverage"]["run"]
    assert (set(run["source"]), run["patch"]) == ({"src", "migrations"}, ["subprocess"])


def test_a99_toolchain_step_expects_every_gate_tool() -> None:
    """A99: шаг инструментов ждёт и `pip-audit`, `mutmut`, `vulture`, `lint-imports`."""
    run = next(
        str(step["run"])
        for _, _, step in steps(load(QUALITY))
        if str(step.get("name", "")).startswith("Инструменты гейтов")
    )
    tools = ("detect-secrets", "ruff", "mypy", "pip-audit", "mutmut", "vulture", "lint-imports")
    assert [tool for tool in tools if f"expect {tool}" not in run] == []


def test_a99_a100_a101_a103_a123_quality_workflow_keeps_the_contract() -> None:
    """A99, A100, A101, A103, A123: `quality.yml` — строгий режим, таймауты, `LINT_*` хуков,
    установка из замка, действия по хешу, права и триггеры, отмена только у запроса."""
    assert quality_check(QUALITY.read_text(encoding="utf-8")) == []


def test_a100_a117_main_guard_keeps_the_contract() -> None:
    """A100, A117: `main-guard.yml` — `issues: write` только у `alert`, тревога от push в main."""
    assert guard_check(GUARD.read_text(encoding="utf-8")) == []


def test_a100_hooks_config_uses_only_local_repos() -> None:
    """A100: внешних `repo:` в конфиге хуков нет."""
    assert hooks_check(HOOKS.read_text(encoding="utf-8")) == []


def test_a104_new_dependencies_are_declared_by_a_human() -> None:
    """A104: каждая строка `new_dependency:` активной поставки — с `by=human:`. Нет активной
    поставки — строк нет, и правило выполнено."""
    text = STATUS.read_text(encoding="utf-8") if STATUS.exists() else ""
    assert status_check(text) == []


def test_a121_lock_has_no_file_newer_than_its_cutoff() -> None:
    """A121: в `uv.lock` нет файла новее отсечки, кроме трёх исключений; отсечка та же, что в
    `[tool.uv]`."""
    lock = tomllib.loads(UV_LOCK.read_text(encoding="utf-8"))
    uv = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["uv"]
    assert lock["options"]["exclude-newer"] == uv["exclude-newer"]
    assert lock_problems(lock) == []


QUALITY_COPIES = [
    (
        "A100-checkout-by-tag",
        r"actions/checkout@[0-9a-f]{40}\s+# v[\d.]+",
        "actions/checkout@v7",
        "не закреплено по коммиту",
    ),
    ("A100-write-all", r"permissions:\n  contents: read", "permissions: write-all", "права токена"),
    (
        "A100-pull-request-target",
        r"\non:\n",
        "\non:\n  pull_request_target:\n",
        "pull_request_target",
    ),
    (
        "A100-secret-in-run",
        r"run: bash scripts/lint/check_gate_coverage.sh",
        "run: echo ${{ secrets.X }}",
        "секреты",
    ),
    (
        "A100-head-ref-in-run",
        r"run: bash scripts/lint/check_gate_coverage.sh",
        "run: echo ${{ github.head_ref }}",
        "поле запроса",
    ),
    (
        "A101-cancel-always",
        r"cancel-in-progress: .*",
        "cancel-in-progress: true",
        "cancel-in-progress",
    ),
    ("A101-main-group-without-sha", r"\|\| github\.sha", "|| github.ref", "без хеша коммита"),
    ("A103-strict-off", r'STRICT: "1"', 'STRICT: "0"', "STRICT"),
    ("A123-other-uv-in-ci", r"uv==\d+\.\d+\.\d+", "uv==0.11.27", "uv==0.11.27"),
    (
        "A99-dead-code-other-area",
        r'LINT_PY_SRC: "backend/src"',
        'LINT_PY_SRC: "backend"',
        "LINT_PY_SRC",
    ),
]


@pytest.mark.parametrize(
    ("case", "old", "new", "named"), QUALITY_COPIES, ids=[c[0] for c in QUALITY_COPIES]
)
def test_quality_copies_are_red_and_named(case: str, old: str, new: str, named: str) -> None:
    """A99, A100, A101, A103, A123, копии: порча `quality.yml` — красный, названо правило."""
    text = QUALITY.read_text(encoding="utf-8")
    spoiled, count = re.subn(old, lambda _: new, text, count=1)
    assert count == 1, f"{case}: в quality.yml нет места порчи {old!r}"
    assert named in "\n".join(quality_check(spoiled))


GUARD_COPIES = [
    ("A117-no-push", r" && github\.event\.workflow_run\.event == 'push'", "", "event == 'push'"),
    (
        "A100-guard-write-on-top",
        r"permissions:\n  contents: read",
        "permissions:\n  contents: read\n  issues: write",
        "права токена",
    ),
]


@pytest.mark.parametrize(
    ("case", "old", "new", "named"), GUARD_COPIES, ids=[c[0] for c in GUARD_COPIES]
)
def test_guard_copies_are_red_and_named(case: str, old: str, new: str, named: str) -> None:
    """A100, A117, копии: порча `main-guard.yml` — красный, названо правило."""
    text = GUARD.read_text(encoding="utf-8")
    spoiled, count = re.subn(old, lambda _: new, text, count=1)
    assert count == 1, f"{case}: в main-guard.yml нет места порчи {old!r}"
    assert named in "\n".join(guard_check(spoiled))


def test_a100_copy_external_hook_repo_is_named() -> None:
    """A100, копия: внешний `repo:` в конфиге хуков — красный, назван репозиторий."""
    spoiled = HOOKS.read_text(encoding="utf-8").replace(
        "- repo: local", "- repo: https://example.invalid/hooks", 1
    )
    assert "https://example.invalid/hooks" in "\n".join(hooks_check(spoiled))


def test_a104_copy_agent_declared_dependency_is_red() -> None:
    """A104, копия: строка `new_dependency:` с `by=agent:` — красный."""
    line = "- **new_dependency:** requests reason=проба by=agent:front-session at=2026-11-20T00:00Z"
    assert "by=human:" in "\n".join(status_check(line))


def test_a121_copy_young_package_without_exception_is_named() -> None:
    """A121, копия: пакет моложе отсечки без исключения — красный: пакет, версия, время."""
    lock = tomllib.loads(UV_LOCK.read_text(encoding="utf-8"))
    victim = next(
        p for p in lock["package"] if p.get("wheels") and p["name"] not in LOCK_EXCEPTIONS
    )
    victim["wheels"][0]["upload-time"] = "2099-01-01T00:00:00Z"
    named = "\n".join(lock_problems(lock))
    assert f"{victim['name']} {victim['version']} загружен 2099-01-01T00:00:00Z" in named


#: Код выката и свои скрипты вне backend/ — под ruff и mypy strict с конфигом backend (решение
#: проверяющего 2026-11-20T09:26Z, строже подписанного). Тела канона — нет: они дословные.
OWN_HOOKS = ("ruff-deploy", "ruff-format-deploy", "mypy-deploy")
OWN_FILES = (
    "deploy/deploy.py",
    "deploy/tools/check.py",
    "scripts/stack_oracle.py",
    "scripts/image_scan.py",
)
CANON_FILES = (
    "scripts/lint/check_ast_gate.py",
    "scripts/delivery_check.py",
    "scripts/merge_guard.sh",
)


def hook(cfg: dict[Any, Any], hook_id: str) -> dict[str, Any]:
    found = [h for repo in cfg["repos"] for h in repo["hooks"] if h["id"] == hook_id]
    return found[0] if found else {}


def own_scripts_problems(cfg: dict[Any, Any]) -> list[str]:
    """Хуки своих скриптов вне backend/: есть, ловят их, тела канона не трогают; конфиг backend."""
    problems = []
    for hook_id in OWN_HOOKS:
        found = hook(cfg, hook_id)
        pattern = re.compile(str(found.get("files", "^$")))
        missed = [f for f in OWN_FILES if not pattern.search(f)]
        canon = [f for f in CANON_FILES if pattern.search(f)]
        config = (
            "--config-file pyproject.toml"
            if hook_id == "mypy-deploy"
            else '--config "$R/backend/pyproject.toml"'
        )
        checks = [
            (bool(found), f"{hook_id}: хука нет"),
            (not missed, f"{hook_id}: не ловит {missed}"),
            (not canon, f"{hook_id}: трогает тела канона {canon}"),
            (config in str(found.get("entry", "")), f"{hook_id}: не конфиг backend"),
        ]
        problems += [message for ok, message in checks if not ok]
    return problems


def run_hook(hook_id: str, target: Path) -> subprocess.CompletedProcess[str]:
    """Запись хука как есть — на файле вне дерева; корень копии даёт `git rev-parse` из ROOT."""
    command = shlex.split(str(hook(load(HOOKS), hook_id)["entry"]))
    return subprocess.run(
        [*command, str(target)], check=False, cwd=ROOT, capture_output=True, text=True, timeout=120
    )


def test_deploy_and_own_scripts_are_under_ruff_and_mypy() -> None:
    """Код выката (`deploy/**/*.py`) и свои скрипты вне backend/ — под ruff, ruff format и mypy
    strict с конфигом backend; тела канона под эти хуки не попадают."""
    assert own_scripts_problems(load(HOOKS)) == []


def test_deploy_copy_ruff_violation_is_red_and_named(tmp_path: Path) -> None:
    """Копия: файл в `deploy/` с командой через оболочку — красный, названо правило S605."""
    probe = tmp_path / "deploy" / "probe.py"
    probe.parent.mkdir()
    probe.write_text(
        'import os\n\n\ndef run(x: str) -> None:\n    os.system("echo " + x)\n', "utf-8"
    )
    done = run_hook("ruff-deploy", probe)
    assert done.returncode != 0 and "S605" in done.stdout, done.stdout + done.stderr


def test_deploy_copy_mypy_violation_is_red_and_named(tmp_path: Path) -> None:
    """Копия: функция в `deploy/` без аннотаций — красный mypy strict, назван код no-untyped-def."""
    probe = tmp_path / "deploy" / "probe.py"
    probe.parent.mkdir()
    probe.write_text("def same(x):\n    return x\n", "utf-8")
    done = run_hook("mypy-deploy", probe)
    assert done.returncode != 0 and "no-untyped-def" in done.stdout, done.stdout + done.stderr
