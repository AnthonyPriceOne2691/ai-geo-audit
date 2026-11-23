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
CALL = (
    "import json, sys; sys.path.insert(0, sys.argv[1]); import delivery_history as h; "
    "print(json.dumps(h.debt_entries(sys.stdin.read())))"
)


def test_a290_exemption_is_not_debt_and_baseline_is() -> None:
    """A290: снимок длины с записью в разделе `[exemption]` и записью в разделе `[baseline]` — в
    долге только запись `[baseline]`."""
    done = subprocess.run(
        [sys.executable, "-I", "-c", CALL, str(SCRIPTS)],
        input=LENGTH,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    assert json.loads(done.stdout) == {"backend/src/pkg/big.py": 640}
