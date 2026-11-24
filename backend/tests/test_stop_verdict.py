"""Разбор остановки `ai_geo_audit.ops.stop_verdict` без Docker (A88, вторая строка): код выхода,
запись uvicorn о конце процесса и команда остановки — ровно один вердикт из трёх."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from ai_geo_audit.ops import stop_verdict as sv

STOPPED, KILLED, CRASHED = sv.Verdict.STOPPED, sv.Verdict.KILLED, sv.Verdict.CRASHED
CASES = [
    (143, True, True, STOPPED, "143-остановка"),
    (0, True, True, STOPPED, "0-остановка"),
    (137, False, True, KILLED, "137-без-записи"),
    (137, True, True, KILLED, "137-с-записью"),
    (143, True, False, CRASHED, "143-без-команды"),
    (143, False, True, CRASHED, "143-без-записи"),
    (0, False, True, CRASHED, "0-без-записи"),
    (0, True, False, CRASHED, "0-без-команды"),
    (1, True, True, CRASHED, "1-с-записью"),
    (137, False, False, CRASHED, "137-без-команды"),
    (142, True, True, CRASHED, "142"),
    (144, True, True, CRASHED, "144"),
    (136, False, True, CRASHED, "136"),
    (138, False, True, CRASHED, "138"),
]


@pytest.mark.parametrize(
    ("code", "finished", "stopped", "expected"),
    [case[:4] for case in CASES],
    ids=[case[4] for case in CASES],
)
def test_a88_stop_is_judged_by_code_record_and_command(
    code: int, finished: bool, stopped: bool, expected: sv.Verdict
) -> None:
    """A88, вторая строка: 143 или 0 с записью после команды остановки — «остановлен»; 137 после
    команды — «убит по сроку»: мягкая остановка не уложилась; 143 без команды и любой другой код
    без записи — «упал». Соседние коды 136, 138, 142 и 144 — тоже «упал»."""
    assert sv.verdict(code, finished=finished, stopped=stopped) is expected


def test_a88_verdicts_read_as_the_example_names_them() -> None:
    """A88, вторая строка: вердикты читаются словами примера — их печатают оракул и сторож
    Ч1.14."""
    assert [verdict.value for verdict in sv.Verdict] == ["остановлен", "убит по сроку", "упал"]


@settings(max_examples=500)
@given(
    code=st.integers(min_value=-1, max_value=300),
    finished=st.booleans(),
    stopped=st.booleans(),
)
def test_a88_any_input_gets_exactly_one_of_three_verdicts(
    code: int, finished: bool, stopped: bool
) -> None:
    """A88, вторая строка, свойство с генератором: на любой код выхода, запись и признак команды —
    ровно один вердикт из трёх, тот, что велит пример: всё, что не «остановлен» и не «убит по
    сроку», — «упал»."""
    holds = {
        STOPPED: stopped and finished and code in (0, 143),
        KILLED: stopped and code == 137,
    }
    holds[CRASHED] = not any(holds.values())
    got = sv.verdict(code, finished=finished, stopped=stopped)
    assert [verdict for verdict, ok in holds.items() if ok] == [got]
