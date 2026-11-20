"""Снимок секретов: записи только у подписанных строк спек; перенос поставки в архив меняет пути.

Правило ведущей 2026-11-20: запись в `.secrets.baseline` — только для того, что править нельзя
(подписанные строки спек под `delivery/`), остальное — пометкой со строкой в решениях: хук сам
переснимает номера строк, и снимок конфликтует между параллельными ветками. Инструмент сдачи
`scripts/secrets_baseline.py` лежит вне пакета и грузится по пути файла.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import envguard

ROOT = envguard.ROOT
_spec = importlib.util.spec_from_file_location(
    "secrets_baseline", ROOT / "scripts" / "secrets_baseline.py"
)
assert _spec is not None
assert _spec.loader is not None
tool = importlib.util.module_from_spec(_spec)
sys.modules["secrets_baseline"] = tool
_spec.loader.exec_module(tool)

ARCHIVE = "delivery/archive/2026-11-20-probe"
#: Инструменты detect-secrets — рядом с интерпретатором окружения тестов.
BIN = Path(sys.executable).parent


def entry(name: str, kind: str, digest: str, line: int = 1) -> dict[str, Any]:
    """Запись снимка в форме detect-secrets 1.5."""
    return {
        "type": kind,
        "filename": name,
        "hashed_secret": digest,
        "is_verified": False,
        "line_number": line,
    }


def outside_delivery(baseline: dict[str, Any]) -> list[str]:
    """Файлы снимка вне `delivery/`: их место — пометка в самом файле, а не запись."""
    return sorted(name for name in baseline["results"] if not name.startswith("delivery/"))


def test_baseline_holds_only_signed_spec_lines() -> None:
    """Записи снимка — только у файлов под `delivery/`; в коде, тестах и настройках — пометки."""
    baseline = json.loads((ROOT / ".secrets.baseline").read_text(encoding="utf-8"))
    assert outside_delivery(baseline) == []


def test_baseline_copy_with_a_test_entry_is_red_and_named() -> None:
    """Копия: новая запись снимка для файла теста — красный, назван файл."""
    baseline = json.loads((ROOT / ".secrets.baseline").read_text(encoding="utf-8"))
    name = "backend/tests/test_logs.py"
    baseline["results"][name] = [entry(name, "Secret Keyword", "a" * 40, 16)]
    assert outside_delivery(baseline) == [name]


def test_move_rewrites_only_paths_under_the_old_directory() -> None:
    """`move`: пути под `delivery/active/` — в архив, у записей тоже; соседний каталог с тем же
    началом имени и чужие файлы не тронуты; пары «тип, хеш» те же."""
    baseline = {
        "results": {
            "backend/x.py": [entry("backend/x.py", "Secret Keyword", "a" * 40)],
            "delivery/active-old/notes.md": [
                entry("delivery/active-old/notes.md", "Secret Keyword", "b" * 40)
            ],
            "delivery/active/spec.md": [
                entry("delivery/active/spec.md", "Basic Auth Credentials", "c" * 40, 11)
            ],
        }
    }
    before = json.loads(json.dumps(baseline))
    assert tool.move(baseline, "delivery/active", ARCHIVE) == 1
    assert list(baseline["results"]) == [
        "backend/x.py",
        "delivery/active-old/notes.md",
        f"{ARCHIVE}/spec.md",
    ]
    assert baseline["results"][f"{ARCHIVE}/spec.md"][0]["filename"] == f"{ARCHIVE}/spec.md"
    assert tool.check(before, baseline) == (
        0,
        "снимок: пары «тип, хеш» те же — 3, меняются только пути",
    )


def test_check_names_a_swallowed_secret_with_its_file() -> None:
    """`check`: снимок после переноса получил новую пару — код 1, названы тип, хеш и файл."""
    before = {
        "results": {"delivery/active/spec.md": [entry("delivery/active/spec.md", "K", "c" * 40)]}
    }
    after = json.loads(json.dumps(before))
    tool.move(after, "delivery/active", ARCHIVE)
    after["results"]["backend/y.py"] = [entry("backend/y.py", "Secret Keyword", "d" * 40)]
    code, line = tool.check(before, after)
    assert code == 1
    assert "пришли ['Secret Keyword dddddddddddd в backend/y.py'], ушли []" in line


def run(cwd: Path, *command: str) -> subprocess.CompletedProcess[str]:
    """Команда во временном репозитории без чужих `GIT_*` из окружения."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    return subprocess.run(
        list(command), cwd=cwd, env=env, capture_output=True, text=True, check=False, timeout=60
    )


def test_handoff_move_keeps_the_hook_green_and_the_pairs(tmp_path: Path) -> None:
    """Сдача по порядку конституции на временном репозитории: после `git mv` в архив хук секретов
    красный; после `move` — зелёный; `check` с коммитом до сдачи — пары те же."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "secrets_baseline.py", repo / "scripts")
    spec = repo / "delivery" / "active" / "spec.md"
    spec.parent.mkdir(parents=True)
    # pragma: allowlist nextline secret
    spec.write_text("| A1 | адрес базы | postgresql://audit:Zx81-kQ2-vB7p@db/audit |\n", "utf-8")
    git = ["git", "-c", "user.name=probe", "-c", "user.email=probe@invalid"]
    git += ["-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"]
    assert run(repo, *git, "init", "-q").returncode == 0
    scan = run(repo, str(BIN / "detect-secrets"), "scan", "delivery/active/spec.md")
    (repo / ".secrets.baseline").write_text(scan.stdout, encoding="utf-8")
    assert run(repo, *git, "add", ".").returncode == 0
    assert run(repo, *git, "commit", "-q", "-m", "до сдачи").returncode == 0
    (repo / "delivery" / "archive").mkdir()
    assert run(repo, *git, "mv", "delivery/active", ARCHIVE).returncode == 0
    hook = [str(BIN / "detect-secrets-hook"), "--baseline", ".secrets.baseline"]
    assert run(repo, *hook, f"{ARCHIVE}/spec.md").returncode == 1
    moved = run(
        repo, sys.executable, "scripts/secrets_baseline.py", "move", "delivery/active", ARCHIVE
    )
    assert "перенесено файлов 1" in moved.stdout, moved.stdout + moved.stderr
    assert run(repo, *git, "add", ".secrets.baseline").returncode == 0
    assert run(repo, *hook, f"{ARCHIVE}/spec.md").returncode == 0
    same = run(repo, sys.executable, "scripts/secrets_baseline.py", "check", "HEAD")
    assert (same.returncode, same.stdout.strip()) == (
        0,
        "снимок: пары «тип, хеш» те же — 1, меняются только пути",
    )
