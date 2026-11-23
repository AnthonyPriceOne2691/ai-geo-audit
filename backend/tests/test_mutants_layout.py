"""Тесты выката собираются в раскладке mutmut: тесты лежат в `backend/mutants/tests/`, и корень
копии там на уровень выше, чем в `backend/tests/`.

Помощник тестов выката обязан найти корень по меткам, как `envguard.repo_root()`, а не по
глубине: иначе шаг статистики mutmut падает на сборе тестов выката (прогон 45150681177).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import envguard

HERE = Path(__file__).resolve().parent
#: Помощники, которые грузит `deploy_fakes` при импорте.
HELPERS = ("deploy_fakes.py", "deploy_records.py", "envguard.py")
LOAD = (
    "import sys; sys.path.insert(0, sys.argv[1]); import deploy_fakes; print(deploy_fakes.SCRIPT)"
)


def test_deploy_fakes_load_script_from_mutants_layout(tmp_path: Path) -> None:
    """Копия помощников в `<корень>/backend/mutants/tests/` грузит `<корень>/deploy/deploy.py`."""
    root = tmp_path / "copy"
    tests = root / "backend" / "mutants" / "tests"
    tests.mkdir(parents=True)
    (root / "docker-compose.dev.yml").write_text("", encoding="utf-8")
    (root / "deploy").mkdir()
    shutil.copy(envguard.ROOT / "deploy" / "deploy.py", root / "deploy" / "deploy.py")
    for name in HELPERS:
        shutil.copy(HERE / name, tests / name)
    argv = [sys.executable, "-I", "-c", LOAD, str(tests)]
    done = subprocess.run(argv, capture_output=True, text=True, check=False, timeout=60)
    assert done.returncode == 0, done.stderr[-600:]
    assert Path(done.stdout.strip()) == (root / "deploy" / "deploy.py").resolve()
