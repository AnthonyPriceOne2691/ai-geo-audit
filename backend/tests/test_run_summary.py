"""A106: итог сессии одной строкой; пропущенный тест красит прогон и называется по имени."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent
PROBE = """
import pytest


def test_passes() -> None:
    assert True


def test_skipped() -> None:
    pytest.skip("проба A106")
"""


def run_probe(tmp_path: Path, body: str) -> subprocess.CompletedProcess[str]:
    (tmp_path / "test_probe.py").write_text(body, encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(TESTS)}
    command = [sys.executable, "-m", "pytest", "-p", "run_summary", "-p", "no:cacheprovider", "-q"]
    return subprocess.run(
        [*command, "test_probe.py"],
        check=False,  # код выхода сверяет сам тест
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_a106_skipped_test_fails_the_run_and_is_named(tmp_path: Path) -> None:
    """A106, копия: тест помечен пропуском — красный, назван тест; строка итога есть."""
    done = run_probe(tmp_path, PROBE)
    assert done.returncode == 1, done.stdout
    assert "собрано 2, прошло 1, пропущено 1" in done.stdout
    assert "пропущен: test_probe.py::test_skipped" in done.stdout


def test_a106_run_without_skips_is_green_with_the_summary(tmp_path: Path) -> None:
    """A106: без пропусков — код 0 и «собрано N, прошло N, пропущено 0»."""
    done = run_probe(tmp_path, PROBE.replace('pytest.skip("проба A106")', "assert True"))
    assert done.returncode == 0, done.stdout
    assert "собрано 2, прошло 2, пропущено 0" in done.stdout
