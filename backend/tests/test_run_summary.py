"""A106: итог сессии одной строкой; любой пропуск — теста или на сборке — красит прогон и
называется по узлу."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent
PYTEST = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q"]
PROBE = """
import pytest


def test_passes() -> None:
    return None


def test_skipped() -> None:
    pytest.skip("проба A106")
"""
#: Проба проверяющей: пропуск модуля и `importorskip` приходят отчётом сборки, а не теста.
COLLECTION_SKIPS = {
    "test_ok.py": "def test_ok() -> None:\n    return None\n",
    "test_modskip.py": 'import pytest\n\npytest.skip("проба A106", allow_module_level=True)\n',
    "test_importskip.py": 'import pytest\n\npytest.importorskip("module_that_is_not_here")\n',
}


def run_in(
    tmp_path: Path, files: dict[str, str], command: list[str]
) -> subprocess.CompletedProcess[str]:
    """Команда подпроцессом во временном каталоге с файлами пробы; плагин виден по PYTHONPATH."""
    for name, body in files.items():
        (tmp_path / name).write_text(body, encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(TESTS)}
    return subprocess.run(
        command,
        check=False,  # код выхода сверяет сам тест
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def run_probe(tmp_path: Path, body: str) -> subprocess.CompletedProcess[str]:
    command = [*PYTEST, "-p", "run_summary", "test_probe.py"]
    return run_in(tmp_path, {"test_probe.py": body}, command)


def test_a106_skipped_test_fails_the_run_and_is_named(tmp_path: Path) -> None:
    """A106, копия: тест помечен пропуском — красный, назван тест; строка итога есть."""
    done = run_probe(tmp_path, PROBE)
    assert done.returncode == 1, done.stdout
    assert "собрано 2, прошло 1, пропущено 1" in done.stdout
    assert "пропущен: test_probe.py::test_skipped" in done.stdout


def test_a106_run_without_skips_is_green_with_the_summary(tmp_path: Path) -> None:
    """A106: без пропусков — код 0 и «собрано N, прошло N, пропущено 0»."""
    done = run_probe(tmp_path, PROBE.replace('pytest.skip("проба A106")', "return None"))
    assert done.returncode == 0, done.stdout
    assert "собрано 2, прошло 2, пропущено 0" in done.stdout


XFAIL = """
import pytest


@pytest.mark.xfail(reason="проба A106", strict=True)
def test_expected_failure() -> None:
    raise AssertionError
"""


def test_a106_xfail_is_not_a_skip(tmp_path: Path) -> None:
    """A106: ожидаемое падение (`xfail`) приходит отчётом с исходом «пропущен», но это не пропуск:
    прогон зелёный, «пропущено 0»."""
    done = run_probe(tmp_path, XFAIL)
    assert "1 xfailed" in done.stdout, done.stdout
    assert done.returncode == 0, done.stdout
    assert "собрано 1, прошло 0, пропущено 0" in done.stdout


def plugin_for(way: str) -> tuple[dict[str, str], list[str]]:
    """Плагин пробе: `-p run_summary` или conftest с импортом хуков списком настоящего conftest."""
    if way == "-p":
        return {}, ["-p", "run_summary"]
    tree = ast.parse((TESTS / "conftest.py").read_text(encoding="utf-8"))
    hooks = [n for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "run_summary"]
    return {"conftest.py": f"{ast.unparse(hooks[0])}\n"}, []


@pytest.mark.parametrize("way", ["-p", "conftest"])
def test_a106_collection_skips_fail_the_run_and_are_named(tmp_path: Path, way: str) -> None:
    """A106, копия пробы проверяющей: пропуск модуля и `importorskip` — пропуски на сборке; они
    красят прогон, названы по узлу и входят в «пропущено N» при обоих способах подключения."""
    files, plugin = plugin_for(way)
    done = run_in(tmp_path, {**COLLECTION_SKIPS, **files}, [*PYTEST, *plugin, "-rs"])
    assert "1 passed, 2 skipped" in done.stdout, done.stdout  # оба пропуска видит и сам pytest
    assert done.returncode == 1, done.stdout
    assert "собрано 1, прошло 1, пропущено 2" in done.stdout
    for module in ("test_modskip.py", "test_importskip.py"):
        assert f"пропущен: {module} — пропуск красит прогон (A106)" in done.stdout


TWO_SESSIONS = """
import sys

import pytest

args = ["-p", "run_summary", "-p", "no:cacheprovider", "-q"]
first = int(pytest.main([*args, *sys.argv[1].split()]))
second = int(pytest.main([*args, *sys.argv[2].split()]))
print(f"коды сессий: {first} {second}")
"""


def test_a106_second_session_in_one_process_counts_afresh(tmp_path: Path) -> None:
    """A106: две сессии pytest в одном процессе (так гоняет mutmut) — счёт у второй свой:
    пропуск первой её не красит, итог второй — только её тесты."""
    files = {"test_probe.py": PROBE, "two_sessions.py": TWO_SESSIONS}
    sessions = ["test_probe.py", "test_probe.py::test_passes"]
    done = run_in(tmp_path, files, [sys.executable, "two_sessions.py", *sessions])
    assert "коды сессий: 1 0" in done.stdout, done.stdout
    assert "собрано 1, прошло 1, пропущено 0" in done.stdout


def test_a106_collection_skip_counts_in_its_own_session_only(tmp_path: Path) -> None:
    """A106: отчёт сборки приходит раньше `pytest_collection_finish`, поэтому счёт обнуляется до
    сборки: пропуск модуля красит первую сессию процесса и не красит вторую."""
    files = {**COLLECTION_SKIPS, "two_sessions.py": TWO_SESSIONS}
    sessions = ["test_ok.py test_modskip.py", "test_ok.py"]
    done = run_in(tmp_path, files, [sys.executable, "two_sessions.py", *sessions])
    assert "коды сессий: 1 0" in done.stdout, done.stdout
    assert "собрано 1, прошло 1, пропущено 1" in done.stdout  # первая сессия
    assert "собрано 1, прошло 1, пропущено 0" in done.stdout  # вторая
