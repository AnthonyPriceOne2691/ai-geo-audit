"""A290: долг на сдаче (`scripts/delivery_history.py`, адаптация «Скорости CI») — запись раздела
`[exemption]` в снимке длины в долг не входит, запись раздела `[baseline]` — входит. Скрипты
Delivery — не пакет: `debt_entries` настоящего скрипта зовётся отдельным процессом."""

from __future__ import annotations

import json
import subprocess
import sys

import envguard

SCRIPTS = envguard.ROOT / "scripts"
#: Снимок длины с записью в каждом разделе — как `file_length_baseline.txt`.
LENGTH = """# file_length_baseline.txt — снимок
[baseline]
640:backend/src/pkg/big.py
[exemption]
580:deploy/deploy.py
"""
#: Снимок без разделов — как `dead_code_baseline.txt`.
DEAD = "# dead_code_baseline.txt — снимок\n1:backend/src/pkg/a.py::name\n"
CALL = (
    "import json, sys; sys.path.insert(0, sys.argv[1]); import delivery_history as h; "
    "print(json.dumps(h.debt_entries(sys.stdin.read())))"
)


def entries(blob: str) -> dict[str, int]:
    """Долг снимка `blob` по `debt_entries` настоящего скрипта."""
    done = subprocess.run(
        [sys.executable, "-I", "-c", CALL, str(SCRIPTS)],
        input=blob,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    found: dict[str, int] = json.loads(done.stdout)
    return found


def test_a290_exemption_is_not_debt_and_baseline_is() -> None:
    """A290: в долг не входит только запись раздела `[exemption]` снимка длины; записи раздела
    `[baseline]` и снимков без разделов (как `dead_code_baseline.txt`) — входят, как у канона.
    Второй вход — страж неизменного поведения канона: правка не ослепляет снимки без разделов."""
    assert (entries(LENGTH), entries(DEAD)) == (
        {"backend/src/pkg/big.py": 640},
        {"backend/src/pkg/a.py::name": 1},
    )
