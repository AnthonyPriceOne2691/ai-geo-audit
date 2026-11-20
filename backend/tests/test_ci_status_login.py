"""Гейт статуса CI и прибор срабатываний — без вывода токена (адаптация проекта, `adapted.json`).

Канон проверял вход командой `gh auth token`: она выводит токен (у канона — в /dev/null), а на
машинах проекта токен не выводится. Адаптация: проверки входа нет, вход проверяет сам вызов
`gh run list`; его отказ (нет входа, сеть) — «статус CI не проверен: причина» с кодом ветки
«нет входа» канона, у прибора срабатываний — «данных о срабатываниях нет: причина», код 0.

Подставной `gh` стоит первым в PATH и пишет каждый вызов в журнал: настоящий `gh` тесты не
зовут, а вызова `auth` нет ни на одном пути.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import envguard

LINT = envguard.ROOT / "scripts" / "lint"
NO_LOGIN = "To get started with GitHub CLI, please run:  gh auth login"
#: Подставной `gh`: журнал вызовов; `auth` отвечает «токеном», отказ — код 4 с текстом `gh`.
FAKE_GH = """#!/bin/bash
printf '%s\\n' "$*" >> "$GH_LOG"
if [[ "$1" == "auth" ]]; then echo "token-probe-must-not-be-read"; exit 0; fi
if [[ -n "${GH_FAIL:-}" ]]; then printf '%s\\n' "$GH_FAIL" >&2; exit 4; fi
printf '%s' "${GH_OUT:-}"
"""
GIT = ["git", "-c", "user.name=probe", "-c", "user.email=probe@invalid"]
GIT += ["-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"]


def clean_env() -> dict[str, str]:
    """Окружение без чужих `GIT_*`."""
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def repo(tmp_path: Path, ci_oracles: str) -> Path:
    """Временный репозиторий с GitHub в origin, двумя скриптами и строкой `ci-oracles`."""
    work = tmp_path / "repo"
    (work / "scripts" / "lint").mkdir(parents=True)
    for name in (
        "check_ci_status.sh",
        "check_gate_value.sh",
        "gate_value_log.py",
        "doctor_hooks.py",
    ):
        shutil.copy(LINT / name, work / "scripts" / "lint" / name)
    status = work / "delivery" / "active" / "STATUS.md"
    status.parent.mkdir(parents=True)
    status.write_text(f"- **ci-oracles:** {ci_oracles}\n", encoding="utf-8")
    for command in (
        ["init", "-q", "-b", "work"],
        ["remote", "add", "origin", "https://github.com/acme/demo.git"],
        ["add", "."],
        ["commit", "-q", "-m", "проба"],
    ):
        subprocess.run([*GIT, *command], cwd=work, env=clean_env(), check=True, timeout=30)
    return work


def head_of(work: Path) -> str:
    """Хеш HEAD временного репозитория."""
    done = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=work,
        env=clean_env(),
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return done.stdout.strip()


def invoke(
    tmp_path: Path, work: Path, script: str, **env: str
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Скрипт во временном репозитории с подставным `gh`: итог и журнал вызовов `gh`."""
    tools = tmp_path / "bin"
    tools.mkdir()
    (tools / "gh").write_text(FAKE_GH, encoding="utf-8")
    (tools / "gh").chmod(0o755)
    log = tmp_path / "gh.log"
    git_dir = Path(shutil.which("git") or "/usr/bin/git").parent
    path = f"{tools}:{git_dir}:/usr/bin:/bin"
    full = {**clean_env(), "PATH": path, "GH_LOG": str(log), **env}
    done = subprocess.run(
        ["bash", str(work / "scripts" / "lint" / script)],
        cwd=work,
        env=full,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return done, calls


def run(
    tmp_path: Path, script: str, ci_oracles: str = "weak", **env: str
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Свежий временный репозиторий и скрипт в нём с подставным `gh`."""
    return invoke(tmp_path, repo(tmp_path, ci_oracles), script, **env)


def no_auth(calls: list[str]) -> bool:
    """Ни одного вызова `gh auth …`: токен не читается и не выводится."""
    return not [call for call in calls if call.split()[:1] == ["auth"]]


def test_ci_status_without_login_is_not_checked_and_never_reads_the_token(tmp_path: Path) -> None:
    """Нет входа: «статус CI не проверен: gh run list — код 4: …», при `ci-oracles: weak` —
    код 0; `gh auth` не вызывался, вызов один — `run list`."""
    done, calls = run(tmp_path, "check_ci_status.sh", GH_FAIL=NO_LOGIN)
    assert done.returncode == 0, done.stderr
    assert f"статус CI не проверен: gh run list — код 4: {NO_LOGIN}" in done.stderr
    assert "`ci-oracles: weak` в STATUS соответствует факту" in done.stdout
    assert no_auth(calls), calls
    assert [call.split()[:2] for call in calls] == [["run", "list"]]


def test_ci_status_without_login_and_deployed_status_is_red(tmp_path: Path) -> None:
    """Нет входа, а в STATUS `ci-oracles: deployed` — красное с причиной, как у канона в ветке
    «нет входа»; `gh auth` не вызывался."""
    done, calls = run(tmp_path, "check_ci_status.sh", "deployed", GH_FAIL=NO_LOGIN)
    assert done.returncode == 1
    assert "а CI не подтверждён: gh run list — код 4" in done.stderr
    assert no_auth(calls), calls


def test_ci_status_green_run_of_this_commit_without_reading_the_token(tmp_path: Path) -> None:
    """Вход есть, последний прогон этого коммита зелёный — «ci-status: OK»; вызов `gh` один —
    `run list` с полями прогона, `gh auth` не вызывался."""
    work = repo(tmp_path, "weak")
    record = {"status": "completed", "conclusion": "success", "url": "https://ci/1"}
    record |= {"headSha": head_of(work), "displayTitle": "проба"}
    done, calls = invoke(
        tmp_path, work, "check_ci_status.sh", GH_OUT=json.dumps([record], separators=(",", ":"))
    )
    assert done.returncode == 0, done.stderr
    assert "ci-status: " in done.stdout
    assert "OK" in done.stdout
    assert calls == [
        "run list --workflow quality --branch work --limit 1"
        " --json status,conclusion,url,headSha,displayTitle"
    ]


def test_gate_value_without_login_has_no_data_and_never_reads_the_token(tmp_path: Path) -> None:
    """Прибор срабатываний, нет входа: «данных о срабатываниях нет: gh run list — код 4: …», код 0,
    а не «нет прогонов»; `gh auth` не вызывался."""
    done, calls = run(tmp_path, "check_gate_value.sh", GH_FAIL=NO_LOGIN)
    assert done.returncode == 0, done.stderr
    assert f"данных о срабатываниях нет: gh run list — код 4: {NO_LOGIN}" in done.stderr
    assert "нет прогонов" not in done.stderr
    assert no_auth(calls), calls


def test_gate_value_with_login_and_no_runs_never_reads_the_token(tmp_path: Path) -> None:
    """Вход есть, прогонов нет — «нет прогонов workflow», код 0; `gh auth` не вызывался."""
    done, calls = run(tmp_path, "check_gate_value.sh", GH_OUT="")
    assert done.returncode == 0, done.stderr
    assert 'нет прогонов workflow "quality"' in done.stderr
    assert no_auth(calls), calls
    assert [call.split()[:2] for call in calls] == [["run", "list"]]
