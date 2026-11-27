"""A290: долг на сдаче (`scripts/delivery_history.py`, адаптация «Скорости CI») — запись раздела
`[exemption]` в снимке длины в долг не входит, запись раздела `[baseline]` — входит. Скрипты
Delivery — не пакет: `debt_entries` настоящего скрипта зовётся отдельным процессом. Страж
настоящего снимка: исключение скрипта выката стоит в `[exemption]` в пределах слова владельца."""

from __future__ import annotations

import json
import subprocess
import sys

import envguard

SCRIPTS = envguard.ROOT / "scripts"
#: Настоящий снимок длины и слово владельца на исключение скрипта выката (показ 6а-3,
#: 2026-11-27T09:10:29Z): снимок не выше 1100, предел 1150.
SNAPSHOT = SCRIPTS / "lint" / "file_length_baseline.txt"
DEPLOY, DEPLOY_CAP = "deploy/deploy.py", 1100
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


def test_a290_deploy_script_snapshot_is_an_exemption_within_the_owner_word() -> None:
    """Снимок длины `deploy/deploy.py` — в `[exemption]`, а не в `[baseline]`, и не выше 1100
    (слово владельца). `--generate` кладёт строку в `[exemption]` только по `is_exempt_path`, а
    там пусто: пересъёмка молча уводит её в `[baseline]` — предел снимок+30 вместо +50, запись —
    долг, который через пять сдач «не растаял» (L102); так вышло на `eacb5b7`."""
    blob = SNAPSHOT.read_text(encoding="utf-8")
    rows = blob.partition("\n[exemption]\n")[2].splitlines()
    exemption = {path: int(size) for size, _, path in (row.partition(":") for row in rows)}
    assert 0 < exemption.get(DEPLOY, 0) <= DEPLOY_CAP, exemption
    assert DEPLOY not in entries(blob)
