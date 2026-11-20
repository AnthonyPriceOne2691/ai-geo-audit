"""Контракт CI (A99–A101, A103, A104, A117, A121, A123): workflow, хуки, STATUS и замок uv.

Тесты читают файлы дерева и в сеть не ходят. Каждое правило — функция, которая возвращает
список нарушений словами: копия-порча называет правило и место, а не падает молча. Правила
workflow судят разобранный YAML, место — путь в нём (номер в списке — с 1); регулярка по тексту
файла — только для комментария версии у `uses`: запись в одну строку прячет `uses:` от регулярки.
"""

from __future__ import annotations

import json
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
#: A100: значение `uses` — `владелец/репо[/путь]@<полный хеш коммита>`; судится разобранное.
PINNED = re.compile(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}")
#: A100: версия — комментарием на строке значения `uses`. Единственная регулярка по тексту файла:
#: в разобранном YAML комментариев нет.
VERSION = re.compile(r"\s#\s*v\d+\.\d+\.\d+\s*$")
EXPORT = re.compile(r'export (LINT_[A-Z_]+)="([^"]*)"')
REQUEST_FIELDS = re.compile(r"\$\{\{\s*github\.(head_ref|event\.pull_request\.)")
#: A100: контекст `secrets` — через точку в любой строке или любым видом внутри `${{ }}`
#: (`secrets['X']`, `toJSON(secrets)`): выражение раннер подставляет до оболочки.
SECRETS = re.compile(r"secrets\.|\$\{\{(?:(?!\}\}).)*?\bsecrets\b", re.IGNORECASE | re.DOTALL)
#: A103: мягкий режим строкой сценария — `STRICT=0`, `STRICT: 0`.
SOFT = re.compile(r"STRICT\s*[:=]\s*['\"]?0")
#: A99: установка в `run:` — pip, pip3, pipx (и через `python -m`, `uv pip`), uvx, npx, `uv tool`,
#: `uv add`, `uv sync`, `uv run --with`, install-команда npm и apt.
INSTALL = re.compile(
    r"(?<![\w-])(?:pip[3x]?|uvx|npx|uv\s+(?:tool|add|sync))(?![\w-])"
    r"|\buv\s+run\b[^\n;&|]*\s--with\b"
    r"|(?<![\w-])(?:npm|apt-get|apt)\b[^\n;&|]*?\s(?:install|i|ci|add)(?![\w-])"
)


def load(path: Path) -> dict[Any, Any]:
    """A99, A100: файл workflow или конфига хуков — словарь YAML."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{path.name}: не словарь YAML"
    return data


def strings(node: Any, place: str = "") -> list[tuple[str, str]]:
    """Каждое значение разобранного YAML строкой, с путём: весь текст `run:` — да, вместе с ` #…`
    сценария; комментариев самого YAML в разобранном нет."""
    if isinstance(node, dict):
        return [found for key, value in node.items() for found in strings(value, f"{place}/{key}")]
    if isinstance(node, list):
        return [found for n, item in enumerate(node, 1) for found in strings(item, f"{place}/{n}")]
    return [(place.lstrip("/"), str(node))]


def steps(workflow: dict[Any, Any]) -> list[tuple[str, dict[Any, Any], dict[Any, Any]]]:
    return [
        (name, job, step) for name, job in workflow["jobs"].items() for step in job.get("steps", [])
    ]


def placed_steps(workflow: dict[Any, Any]) -> list[tuple[str, dict[Any, Any]]]:
    """Шаги с местом — путём `jobs/<джоба>/steps/<N>`, как у `strings`."""
    return [
        (f"jobs/{name}/steps/{number}", step)
        for name, job in workflow["jobs"].items()
        for number, step in enumerate(job.get("steps", []), 1)
    ]


def uses_values(workflow: dict[Any, Any]) -> list[tuple[str, Any]]:
    """A100: каждое значение `uses` разобранного YAML с местом — у джобы и у шагов."""
    jobs = [
        (f"jobs/{name}/uses", job["uses"])
        for name, job in workflow["jobs"].items()
        if "uses" in job
    ]
    return jobs + [
        (f"{at}/uses", step["uses"]) for at, step in placed_steps(workflow) if "uses" in step
    ]


def runs(workflow: dict[Any, Any]) -> list[tuple[str, str]]:
    """Текст `run:` каждого шага с местом — целиком, с комментариями сценария."""
    return [(f"{at}/run", str(step["run"])) for at, step in placed_steps(workflow) if "run" in step]


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


def strict_problems(workflow: dict[Any, Any]) -> list[str]:
    """A99, A103: режим строгий везде — в `env` и в тексте `run:`; у каждой джобы предохранитель
    по времени."""
    problems = [] if workflow.get("env", {}).get("STRICT") == "1" else ['STRICT: сверху не "1"']
    for place, value in strings(workflow):
        if (place.endswith("env/STRICT") and value != "1") or SOFT.search(value):
            problems.append(f"STRICT: {place} включает мягкий режим")
    for name, job in workflow["jobs"].items():
        if not isinstance(job.get("timeout-minutes"), int):
            problems.append(f"timeout-minutes: у джобы {name} нет предохранителя")
    return problems


def install_problems(workflow: dict[Any, Any], uv_version: str) -> list[str]:
    """A99, A123: в `run:` нет установок, кроме закреплённых, что стоят в файлах сейчас, — uv
    точной версии пакетом и dev-группа из замка. Закреплённое вырезается целой командой, остаток
    судится."""
    pinned = re.compile(
        rf"(?:python -m pip install uv=={re.escape(uv_version)}|uv sync --frozen)"
        r"(?=[ \t]*(?:$|&&|;)|[ \t]+#)"
    )
    return [
        f"установка: {place} — `{line.strip()}`; закреплены только uv=={uv_version}"
        " и `uv sync --frozen`"
        for place, run in runs(workflow)
        for line in run.replace("\\\n", " ").splitlines()
        if INSTALL.search(pinned.sub("", line))
    ]


def frozen_problems(workflow: dict[Any, Any]) -> list[str]:
    """A99, A123: инструменты гейтов — dev-группа из замка: в `run:` есть `uv sync --frozen`."""
    frozen = any("uv sync --frozen" in run for _, run in runs(workflow))
    return [] if frozen else ["установка: нет `uv sync --frozen`"]


def uses_problems(text: str, workflow: dict[Any, Any]) -> list[str]:
    """A100: каждое `uses` разобранного YAML — у джобы и у шагов, блоком, в одну строку или якорем —
    по полному хешу коммита."""
    values = uses_values(workflow)
    pinned = [value for _, value in values if isinstance(value, str) and PINNED.fullmatch(value)]
    return [
        *(
            f"не закреплено по коммиту: {at} = {value}"
            for at, value in values
            if value not in pinned
        ),
        *version_problems(text, set(pinned)),
    ]


def version_problems(text: str, pinned: set[str]) -> list[str]:
    """A100: на каждой строке текста с закреплённым значением `uses` — комментарий `# vX.Y.Z`;
    строки-комментарии YAML не в счёт."""
    lines = [
        (n, line)
        for n, line in enumerate(text.splitlines(), 1)
        if not line.lstrip().startswith("#")
    ]
    problems = []
    for value in sorted(pinned):
        found = [(n, line) for n, line in lines if value in line]
        if not found:
            problems.append(f"версия: значение {value} не найдено ни в одной строке")
        problems += [
            f"версия: строка {n} без комментария # vX.Y.Z — {line.strip()}"
            for n, line in found
            if not VERSION.search(line)
        ]
    return problems


def secret_problems(workflow: dict[Any, Any]) -> list[str]:
    """A100: контекста `secrets` нет ни в одной строке разобранного YAML, и во всём тексте `run:`:
    там ` #` — текст сценария, а выражение раннер подставляет до оболочки. Комментарии самого YAML
    не в счёт. Токен прогона — `github.token`, а не `secrets.GITHUB_TOKEN`."""
    return [
        f"секреты: {place} читает secrets"
        for place, value in strings(workflow)
        if SECRETS.search(value)
    ]


def token_problems(workflow: dict[Any, Any], writers: dict[str, str]) -> list[str]:
    """A100: сверху только чтение, запись — у названных джоб; без чужих триггеров; службы — по
    дайджесту."""
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
    return problems + [
        f"служба джобы {name}: образ не по дайджесту"
        for name, job in workflow["jobs"].items()
        for service in job.get("services", {}).values()
        if "@sha256:" not in str(service.get("image", ""))
    ]


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
        *uses_problems(text, workflow),
        *token_problems(workflow, {}),
        *secret_problems(workflow),
        *concurrency_problems(workflow),
        *strict_problems(workflow),
        *install_problems(workflow, uv_version()),
        *frozen_problems(workflow),
        *lint_env_problems(workflow, hook_exports()),
    ]


def guard_check(text: str) -> list[str]:
    workflow = yaml.safe_load(text)
    return [
        *uses_problems(text, workflow),
        *token_problems(workflow, {"alert": "issues"}),
        *secret_problems(workflow),
        *install_problems(workflow, uv_version()),
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
    # Находки проверяющей 2026-11-20: запись `uses` в одну строку, `secrets.` за ` #` внутри
    # `run: |`, `pip3 install` (A99 — копии ниже, `INSTALLS`); и порчи, которые краснели и прежде.
    (
        "A100-uses-in-one-line",
        r"- uses: actions/checkout@[0-9a-f]{40}  # v[\d.]+\n\s+with:\n[^\n]*\n"
        r"[^\n]*persist-credentials: false[^\n]*",
        "- {uses: actions/checkout@v7, with: {fetch-depth: 0, persist-credentials: false}}",
        "не закреплено по коммиту: jobs/gates/steps/1/uses = actions/checkout@v7",
    ),
    (
        "A100-job-uses-by-tag",
        r"\njobs:\n",
        "\njobs:\n  reuse:\n    uses: octo-org/flows/.github/workflows/ci.yml@v1\n",
        "не закреплено по коммиту: jobs/reuse/uses = octo-org/flows/.github/workflows/ci.yml@v1",
    ),
    (
        "A100-version-not-in-comment",
        r"  # v[\d.]+\n",
        "\n",
        "без комментария # vX.Y.Z — - uses: actions/checkout@",
    ),
    (
        "A100-secret-after-hash-in-run-block",
        r"run: bash scripts/lint/check_gate_coverage.sh",
        "run: |\n          bash scripts/lint/check_gate_coverage.sh # ${{ secrets.X }}",
        "секреты: jobs/gates/steps/7/run",
    ),
    (
        "A100-secrets-whole-context",
        r"run: bash scripts/lint/check_gate_coverage.sh",
        "run: echo '${{ toJSON(secrets) }}'",
        "секреты: jobs/gates/steps/7/run",
    ),
    (
        "A100-persist-credentials-dropped",
        r"\n\s+persist-credentials: false[^\n]*",
        "",
        "checkout: токен сохраняется",
    ),
    (
        "A103-strict-off-after-quoted-hash",
        r"run: bash scripts/lint/check_gate_coverage.sh",
        "run: |\n          echo ' #'; STRICT=0 bash scripts/lint/check_gate_coverage.sh",
        "STRICT: jobs/gates/steps/7/run",
    ),
    (
        "A103-job-without-timeout",
        r"\n    timeout-minutes: 20",
        "",
        "timeout-minutes: у джобы gates нет предохранителя",
    ),
    (
        "A123-uv-sync-without-frozen",
        r"uv sync --frozen",
        "uv sync",
        "установка: jobs/gates/steps/4/run — `cd backend && uv sync`",
    ),
]


@pytest.mark.parametrize(
    ("case", "old", "new", "named"), QUALITY_COPIES, ids=[c[0] for c in QUALITY_COPIES]
)
def test_quality_copies_are_red_and_named(case: str, old: str, new: str, named: str) -> None:
    """A99, A100, A101, A103, A123, копии: порча `quality.yml` — красный, названо правило; у порч
    записи, `run:` и установки — и место, путь в YAML."""
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
    (
        "A100-guard-token-from-secrets",
        r"\$\{\{ github\.token \}\}",
        "${{ secrets.GITHUB_TOKEN }}",
        "секреты: jobs/alert/steps/1/env/GH_TOKEN",
    ),
    (
        "A100-guard-uses-by-tag",
        r"- run: exit 1",
        "- uses: actions/checkout@v7",
        "не закреплено по коммиту: jobs/alert/steps/2/uses = actions/checkout@v7",
    ),
    (
        "A99-guard-install",
        r"- run: exit 1",
        "- run: npm i -g left-pad && exit 1",
        "установка: jobs/alert/steps/2/run",
    ),
]


@pytest.mark.parametrize(
    ("case", "old", "new", "named"), GUARD_COPIES, ids=[c[0] for c in GUARD_COPIES]
)
def test_guard_copies_are_red_and_named(case: str, old: str, new: str, named: str) -> None:
    """A99, A100, A117, копии: порча `main-guard.yml` — красный, названо правило; правила `uses`,
    секретов и установки — те же, что у `quality.yml`."""
    text = GUARD.read_text(encoding="utf-8")
    spoiled, count = re.subn(old, lambda _: new, text, count=1)
    assert count == 1, f"{case}: в main-guard.yml нет места порчи {old!r}"
    assert named in "\n".join(guard_check(spoiled))


COVERAGE = "bash scripts/lint/check_gate_coverage.sh"
#: A99: установка в `run:` мимо закреплённых — каждая красная; первая — проба проверяющей.
INSTALLS = (
    "pip3 install requests",
    "pip install pre-commit",
    "python3 -m pip install requests",
    "python -m pip install uv==0.11.26 requests",
    "uv pip install requests",
    "uv tool install ruff",
    "uvx ruff check",
    "pipx install ruff",
    "uv add requests",
    "uv run --no-sync --with requests python -V",
    "npm install left-pad",
    "npm i left-pad",
    "npm --prefix frontend ci",
    "npx eslint .",
    "sudo apt-get -y install curl",
)


@pytest.mark.parametrize("command", INSTALLS, ids=[c.replace(" ", "-") for c in INSTALLS])
def test_a99_copy_install_in_run_is_red_and_named(command: str) -> None:
    """A99, копии: установка в `run:` мимо закреплённых (uv точной версии, `uv sync --frozen`) —
    красный; названы правило, место и команда."""
    text = QUALITY.read_text(encoding="utf-8")
    spoiled = text.replace(f"run: {COVERAGE}", f"run: {command} && {COVERAGE}", 1)
    assert spoiled != text, f"в quality.yml нет места порчи `run: {COVERAGE}`"
    named = f"установка: jobs/gates/steps/7/run — `{command} && {COVERAGE}`"
    assert named in "\n".join(quality_check(spoiled))


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
#: проверяющего 2026-11-20T09:26Z, строже подписанного). Тела канона — нет: они дословные, их
#: перечень — замок payload. Свой файл узнаётся сам: каждый `.py` в `scripts/` и `deploy/` вне
#: замка, и имена, которых в дереве ещё нет, — новый скрипт не ждёт правки шаблона (ведущая,
#: 2026-11-20).
OWN_HOOKS = ("ruff-deploy", "ruff-format-deploy", "mypy-deploy")
CANON_LOCK = ROOT / "scripts" / "lint" / "payload.lock.json"
FUTURE_FILES = (
    "deploy/deploy.py",
    "deploy/tools/check.py",
    "scripts/stack_oracle.py",
    "scripts/stack_oracle_compose.py",
    "scripts/image_scan.py",
    "scripts/oracle_guard.py",
    "scripts/tools/new_script.py",
)


def hook(cfg: dict[Any, Any], hook_id: str) -> dict[str, Any]:
    found = [h for repo in cfg["repos"] for h in repo["hooks"] if h["id"] == hook_id]
    return found[0] if found else {}


def canon_bodies() -> set[str]:
    """Тела канона по замку payload — пути, которых хуки своих скриптов не трогают."""
    return set(json.loads(CANON_LOCK.read_text(encoding="utf-8"))["scripts"])


def own_files() -> list[str]:
    """Свои `.py` в `scripts/` и `deploy/`: файлы копии и имена, которых ещё нет, — вне замка."""
    done = subprocess.run(
        ["git", "ls-files", "scripts/*.py", "deploy/*.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return sorted((set(done.stdout.split()) | set(FUTURE_FILES)) - canon_bodies())


def caught_by(found: dict[str, Any], path: str) -> bool:
    """Путь попадает в хук, как у pre-commit: подходит под `files` и не подходит под `exclude`."""
    include, exclude = str(found.get("files", "^$")), str(found.get("exclude", "^$"))
    return bool(re.search(include, path)) and not re.search(exclude, path)


def own_scripts_problems(cfg: dict[Any, Any], own: list[str] | None = None) -> list[str]:
    """Хуки своих скриптов вне backend/: есть, ловят каждый свой `.py`, тела канона не трогают;
    конфиг backend."""
    own = own_files() if own is None else own
    problems = []
    for hook_id in OWN_HOOKS:
        found = hook(cfg, hook_id)
        missed = [f for f in own if not caught_by(found, f)]
        canon = sorted(f for f in canon_bodies() if caught_by(found, f))
        config = (
            "--config-file pyproject.toml"
            if hook_id == "mypy-deploy"
            else '--config "$R/backend/pyproject.toml"'
        )
        checks = [
            (bool(found), f"{hook_id}: хука нет"),
            (not missed, f"{hook_id}: свой файл вне хука {missed}"),
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
    assert {"scripts/delivery_check.py", "scripts/lint/check_ast_gate.py"} <= canon_bodies()
    assert own_scripts_problems(load(HOOKS)) == []


OWN_COPIES = [
    (
        "files-by-names",
        "files",
        r"^(deploy/.*|scripts/(stack_oracle|image_scan))\.py$",
        "scripts/stack_oracle_compose.py",
    ),
    ("exclude-too-wide", "exclude", r"^scripts/.*\.py$", "scripts/oracle_guard.py"),
    ("exclude-without-delivery", "exclude", r"^scripts/lint/.*\.py$", "scripts/delivery_check.py"),
]


@pytest.mark.parametrize(
    ("case", "key", "value", "named"), OWN_COPIES, ids=[c[0] for c in OWN_COPIES]
)
def test_own_scripts_copies_are_red_and_named(case: str, key: str, value: str, named: str) -> None:
    """Копии: шаблон по перечню имён не видит новый кусок оракула; исключение шире канона глотает
    свой скрипт; исключение без `delivery_*` трогает тело канона — красный, названо имя файла."""
    cfg = load(HOOKS)
    for hook_id in OWN_HOOKS:
        hook(cfg, hook_id)[key] = value
    assert named in "\n".join(own_scripts_problems(cfg)), case


def test_own_script_named_like_canon_is_red_and_named() -> None:
    """Свой скрипт с именем как у тел канона (`scripts/delivery_*.py`), но вне замка — красный:
    исключение хуков не прячет свой код."""
    named = "\n".join(own_scripts_problems(load(HOOKS), own=["scripts/delivery_extra.py"]))
    assert "свой файл вне хука ['scripts/delivery_extra.py']" in named


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
