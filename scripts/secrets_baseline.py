"""Снимок секретов при переносе поставки в архив: меняются пути, пары «тип, хеш» — нет.

Запись в `.secrets.baseline` держат только подписанные строки спек: их править нельзя, всё
остальное — пометкой `# pragma: allowlist secret` со строкой в решениях (ведущая, 2026-11-20).
Записи снимка привязаны к пути файла, поэтому при сдаче, когда `delivery/active/*` уезжает в
`delivery/archive/<дата>-<имя>/`, хук секретов на коммите сдачи краснеет.

    python3 scripts/secrets_baseline.py move delivery/active delivery/archive/<дата>-<имя>
    python3 scripts/secrets_baseline.py check <коммит до сдачи> [<коммит сдачи>]

`move` переписывает пути только под старым каталогом и больше ничего не трогает. `check`
сверяет пары «тип, хеш» двух снимков; второй по умолчанию — рабочего дерева. Пары разные —
код 1 и названы пришедшие и ушедшие пары с файлами: обновление проглотило новый секрет.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

BASELINE = ".secrets.baseline"
ROOT = Path(__file__).resolve().parents[1]

Pair = tuple[str, str]


def load(rev: str | None) -> dict[str, Any]:
    """Снимок из коммита `rev` или, без него, из рабочего дерева."""
    if rev is None:
        data: dict[str, Any] = json.loads((ROOT / BASELINE).read_text(encoding="utf-8"))
        return data
    done = subprocess.run(  # noqa: S603 -- git со своим корнем; `rev` — ревизия, не оболочка
        ["git", "-C", str(ROOT), "show", f"{rev}:{BASELINE}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if done.returncode:
        raise SystemExit(f"снимок: в {rev} снимка нет — {done.stderr.strip()}")
    parsed: dict[str, Any] = json.loads(done.stdout)
    return parsed


def pairs(baseline: dict[str, Any]) -> Counter[Pair]:
    """Пары «тип, хеш» снимка с числом повторов: пути в счёт не идут."""
    return Counter(
        (entry["type"], entry["hashed_secret"])
        for entries in baseline["results"].values()
        for entry in entries
    )


def files_of(baseline: dict[str, Any], wanted: Counter[Pair]) -> list[str]:
    """Пары с файлами, где они записаны: «тип хеш в файл»."""
    return sorted(
        f"{entry['type']} {entry['hashed_secret'][:12]} в {name}"
        for name, entries in baseline["results"].items()
        for entry in entries
        if (entry["type"], entry["hashed_secret"]) in wanted
    )


def move(baseline: dict[str, Any], old: str, new: str) -> int:
    """Переписать пути под `old/` на `new/`; вернуть число перенесённых файлов."""
    old_dir, new_dir = old.rstrip("/") + "/", new.rstrip("/") + "/"
    results: dict[str, Any] = {}
    moved = 0
    for name, entries in baseline["results"].items():
        target = new_dir + name[len(old_dir) :] if name.startswith(old_dir) else name
        if target in results:
            raise SystemExit(f"снимок: {target} уже записан — перенос слил бы два файла")
        moved += target != name
        for entry in entries:
            entry["filename"] = target
        results[target] = entries
    baseline["results"] = dict(sorted(results.items()))
    return moved


def check(before: dict[str, Any], after: dict[str, Any]) -> tuple[int, str]:
    """Сверка пар двух снимков: код и строка итога."""
    old, new = pairs(before), pairs(after)
    if old == new:
        return 0, f"снимок: пары «тип, хеш» те же — {sum(new.values())}, меняются только пути"
    came, gone = files_of(after, new - old), files_of(before, old - new)
    return 1, f"снимок: пары разные — пришли {came}, ушли {gone}"


def main(argv: list[str]) -> int:
    if len(argv) == 3 and argv[0] == "move":
        baseline = load(None)
        moved = move(baseline, argv[1], argv[2])
        if moved:
            text = json.dumps(baseline, indent=2) + "\n"
            (ROOT / BASELINE).write_text(text, encoding="utf-8")
            print(f"снимок: перенесено файлов {moved}, {argv[1]} → {argv[2]}")
        else:
            print(f"снимок: записей под {argv[1]} нет — снимок не тронут")
        return 0
    if len(argv) in (2, 3) and argv[0] == "check":
        code, line = check(load(argv[1]), load(argv[2] if len(argv) == 3 else None))
        print(line)
        return code
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
