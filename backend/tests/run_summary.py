"""A106: строка итога «собрано N, прошло N, пропущено S»; пропуск красит прогон.

Не флаг CI: правило одно на машине и в CI. Подключает `conftest.py` (хуки по именам), тест
гоняет этот плагин отдельно — в подпроцессе pytest на временном файле.
"""

from __future__ import annotations

import pytest

#: Итоги сессии: что собрано, прошло и пропущено.
OUTCOME: dict[str, list[str]] = {"passed": [], "skipped": []}
COLLECTED: list[int] = [0]


def pytest_collection_finish(session: pytest.Session) -> None:
    """Начало счёта сессии: mutmut гоняет несколько сессий в одном процессе."""
    COLLECTED[0] = len(session.items)
    OUTCOME["passed"].clear()
    OUTCOME["skipped"].clear()


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if report.skipped and not hasattr(report, "wasxfail"):
        OUTCOME["skipped"].append(report.nodeid)
    elif report.when == "call" and report.passed:
        OUTCOME["passed"].append(report.nodeid)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Пропуск — не зелёный прогон: код выхода ненулевой."""
    if OUTCOME["skipped"] and exitstatus == pytest.ExitCode.OK:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    passed, skipped = OUTCOME["passed"], OUTCOME["skipped"]
    line = f"собрано {COLLECTED[0]}, прошло {len(passed)}, пропущено {len(skipped)}"
    terminalreporter.write_line(line)
    for nodeid in skipped:
        terminalreporter.write_line(f"пропущен: {nodeid} — пропуск красит прогон (A106)")
