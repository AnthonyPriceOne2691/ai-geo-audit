#!/usr/bin/env python3
"""Delivery harness checker.

Usage:
  python scripts/delivery_check.py
  python scripts/delivery_check.py --require-spec

Exit 0 = OK (warnings allowed). Exit 1 = errors.
"""


from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

# Части — с места входа, а не из пути поиска (`delivery@2.24`): `python3 -I` убирает каталог
# скрипта из `sys.path`, `-P` — тоже, оставляя `PYTHONPATH`, и части приходили из чужого дерева.
PARTS_DIR = str(Path(__file__).absolute().parent)
if sys.path[:1] != [PARTS_DIR]:
    sys.path.insert(0, PARTS_DIR)

from delivery_base import (ACTIVE, ARCHIVE, DELIVERY, ROOT,
                           field, git,
                           is_placeholder, merge_base_of, read, window_refusal)
from delivery_decisions import check_permissions
from delivery_diff import archived_in_diff
from delivery_evidence import check_evidence
from delivery_journals import check_journals
from delivery_limits import check_limits
from delivery_status import check_status_shape

def check_archive_index(args, errors: list[str], warnings: list[str]) -> None:
    """Индекс архива: полнота и просроченное наблюдение (§2.2a, §13.1).

    Шов `main` (`delivery@1.59`). Границы заданы ТЕКСТОМ первой и следующей
    строки, а не маркером: в `main` маркеры стоят на разных отступах и
    внутри веток, поэтому «до следующего маркера» здесь неверно.
    """
    # --- §2.2a: архив без индекса не читается, а отставший индекс врёт о полноте.
    # Обе проверки не зависят от фазы и от наличия diff-base: они про сам архив.
    if ARCHIVE.is_dir():
        # Каталог-ШАБЛОН пропускается, и это не поблажка: канон сам
        # поставляет `archive/<slug>/observed.md` как образец записи
        # наблюдения (§13). Без пропуска `delivery_check` краснеет сразу
        # после развёртывания на ЧИСТОМ проекте — гейт объявляет
        # неиндексированной поставкой файл, который положил сам канон.
        # Найдено развёртыванием в пустой проект (2026-08-17): тот же
        # класс, что `cqg@2.16` — канон не попадает в раскладку, которую
        # сам предписывает.
        shipments = sorted(d.name for d in ARCHIVE.iterdir()
                           if d.is_dir() and not d.name.startswith("<"))
        idx_text = read(ARCHIVE / "INDEX.md")
        if shipments and not idx_text:
            errors.append(
                f"delivery/archive/ содержит {len(shipments)} поставок, а "
                "INDEX.md нет (§2.2a / A.13) — неиндексированный архив никем "
                "не читается"
            )
        elif idx_text:
            unlisted = [s for s in shipments if s not in idx_text]
            if unlisted:
                errors.append(
                    "archive/INDEX.md отстал от архива: не упомянуты "
                    f"{', '.join(unlisted)} (§2.2a) — индекс, который врёт о "
                    "своей полноте, хуже отсутствующего"
                )

        check_overdue_observation(shipments, errors)


def check_preconditions(args, errors: list[str], warnings: list[str]) -> bool:
    """Каркас на месте — иначе проверять дальше нечего.

    ⚠ Возвращает ПРОДОЛЖАТЬ ЛИ, а не данные, и это не стиль. Из блока убегало не
    имя, а ПОТОК УПРАВЛЕНИЯ: внутри стоял `return 1` — досрочный выход из `main`.
    Механический вырез унёс его в помощника, где он значил «проверка прошла», и
    75 тестов упали (`delivery@1.59`). Новый класс к процедуре резки: смотреть
    надо не только на имена, но и на `return`/`break`/`continue`, пересекающие
    границу шва.
    """
    if not (DELIVERY / "CONSTITUTION.md").is_file():
        errors.append("missing delivery/CONSTITUTION.md")
    if not ACTIVE.is_dir():
        errors.append("missing delivery/active/")
        for w in warnings:
            print(f"WARNING: {w}")
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        print(f"delivery_check: {len(errors)} error(s), {len(warnings)} warning(s)")
        return False

    status = read(ACTIVE / "STATUS.md")
    if not status:
        # Пустой active/ — ШТАТНОЕ состояние (§2.3a): предыдущая поставка
        # заархивирована, следующая не начата. Ошибкой это быть не может, иначе
        # контур красный между поставками — причём §13.1 сам загоняет проект в
        # это состояние, требуя архивации на handoff (полевая находка F12).
        # Проверки уровня архива ниже продолжают работать: именно в idle всплывает
        # просроченное наблюдение.
        warnings.append(
            "idle: нет delivery/active/STATUS.md — активной поставки нет (§2.3a). "
            "Начинаешь работу? Создай STATUS по шаблону A.2"
        )
    # §2.3a: поставку, которую архивирует ЭТОТ дифф, судят те же разделы.
    base = getattr(args, "diff_base", None)
    archived = archived_in_diff(base)
    check_window(base, archived, not status, errors)
    for home in ([ACTIVE] if status else []) + archived:
        judge_delivery(home, args, errors, warnings)
    return True


def check_window(base: str | None, archived: list[Path], idle: bool,
                 errors: list[str]) -> None:
    """Окно ветки: простой называет, какое судил; база без окна — отказ (`delivery@2.23`).

    «idle, 0 ошибок» печаталось одинаково — смотрел гейт в окно или не смог его
    построить (`delivery@2.09`). Отказ стоял только под `--require-ci` и в простое, а у
    активной поставки база без окна молча снимала проверки по диффу: первый проект на
    `cqg@3.14` (18.11) — в свежем клоне `--diff-base main` дал 2 предупреждения вместо 4,
    о базе ни слова. Не судить по диффу — значит не передавать базу.
    """
    if base is None:
        if idle:
            print("простой: окна диффа нет (без --diff-base) — поставку, которую "
                  "архивирует дифф, этот прогон не судит (§2.3a)")
        return
    start = merge_base_of(base)
    if not start:
        errors.append(f"окно диффа не определено: {window_refusal(base)} — проверки по "
                      "диффу (breakers §3.4, рисковый дифф §12.5, цена решений §12.2a, "
                      "порядок §3.1d) и поставку, которую архивирует дифф (§2.3a), судить "
                      "нечем: это не «нарушений нет» (в CI базу даёт шаг «Diff base», CQG §8.3)")
        return
    if idle:
        print(f"простой: окно {start[:12]}..HEAD (база {base}) — "
              f"поставок, которые архивирует этот дифф: {len(archived)}")


#: Архив в окне, где уже идёт другая поставка (`delivery@2.26`; первый проект на `delivery@2.21`:
#: архив bootstrap первым коммитом ветки следующей получил 2 ошибки по её конфигу и логам).
FOREIGN_ARCHIVE = (
    "архив судится по всему окну, а в нём уже идёт другая поставка (active/STATUS.md): её код "
    "поднимает классы риска §12.5 этому архиву. Архивируй на handoff в ветке самой поставки "
    "(§2.3a); архив bootstrap — отдельным коммитом с отдельной приёмкой (§7.2 п.6)")


def judge_delivery(home: Path, args, errors: list[str], warnings: list[str]) -> None:
    """Все разделы гейта над ОДНОЙ поставкой — на её фазе; у архивной путь в каждой строке."""
    status = read(home / "STATUS.md")
    tag = "" if home == ACTIVE else f"{home.relative_to(ROOT).as_posix()}: "
    errs: list[str] = []
    warns: list[str] = []
    if tag:
        print(f"судится поставка, которую архивирует этот дифф: {tag[:-2]} "
              f"(phase={field(status, 'phase') or '?'})")
        if read(ACTIVE / "STATUS.md"):
            warns.append(FOREIGN_ARCHIVE)
    ctx = check_status_shape(status, args, errs, warns, home)
    check_evidence(status, args, errs, warns, ctx)
    check_journals(status, args, errs, warns, ctx)
    check_limits(status, args, errs, warns, ctx)
    errors.extend(tag + e for e in errs)
    warnings.extend(tag + w for w in warns)


def parse_args():
    """Разбор аргументов — отдельно, чтобы `main` остался диспетчером."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--require-spec", action="store_true")
    ap.add_argument("--require-verify", action="store_true")
    ap.add_argument(
        "--require-ci",
        action="store_true",
        help="fail unless ci-oracles: deployed (§10.4)",
    )
    ap.add_argument(
        "--diff-base",
        metavar="REF",
        help="check circuit breakers §3.4 against REF (e.g. origin/main)",
    )
    return ap.parse_args()


def check_overdue_observation(shipments, errors: list[str]) -> None:
    """Просроченное наблюдение по индексу архива (§13.1).

    Шов по сложности (`delivery@1.59`): `check_archive_index` давала cx 14.
    Печатает, сколько окон судила (`delivery@2.09`): «просрочено 0» без числа
    окон не отличает «все закрыты» от «срока не нашлось ни у кого».
    """
    # --- §13.1: просроченное наблюдение. Лаг в одну поставку — проверка
    # срабатывает тогда, когда человек всё равно смотрит в PR. Дыра названа
    # в каноне: если следующей поставки не будет, никто не проверит; на это
    # тот же периодический прогон, что okf_sync_gate --check-stale.
    today = date.today().isoformat()
    windows = overdue = 0
    for name in shipments:
        until = field(read(ARCHIVE / name / "STATUS.md"), "observe_until")
        due = None if is_placeholder(until) else re.match(r"(\d{4}-\d{2}-\d{2})", until)
        if not due:
            continue
        windows += 1
        if due.group(1) <= today and not (ARCHIVE / name / "observed.md").is_file():
            overdue += 1
            errors.append(
                f"наблюдение просрочено: {name} — observe_until={due.group(1)} "
                f"(сегодня {today}), а observed.md нет (§13.1 / A.17). "
                "Проверь сигнал в проде и закрой наблюдение, либо сдвинь срок "
                "с причиной"
            )
    print(f"наблюдение §13.1: поставок в архиве {len(shipments)}, окон со сроком "
          f"{windows}, истекших без observed.md {overdue}")


def check_layer_pointers(warnings: list[str]) -> None:
    """Соседние слои развёрнуты — CONSTITUTION обязан на них указывать."""
    if (ROOT / "CODE_QUALITY_GATES.md").is_file():
        cons = read(DELIVERY / "CONSTITUTION.md")
        if "CODE_QUALITY_GATES" not in cons:
            warnings.append("CQG present but CONSTITUTION.md has no pointer")
    if (ROOT / "OKF_KNOWLEDGE_BUNDLE.md").is_file() or (ROOT / "knowledge").is_dir():
        cons = read(DELIVERY / "CONSTITUTION.md")
        if "OKF" not in cons and "knowledge/" not in cons:
            warnings.append("OKF/knowledge present but CONSTITUTION.md has no pointer")


def check_smoke_table(warnings: list[str]) -> None:
    """Таблица сквозных проверок после первой поставки кода (§6.1, `delivery@2.23`).

    Первый проект на `cqg@3.14`, 20.11: README с bootstrap обещал сценарии «с первой
    поставкой каркаса», каркас ушёл в архив, таблица пуста, `run.sh` нет, а `merge_guard`
    шаг пропускал — обещание без исполнителя. Первая поставка кода — в архиве есть
    поставка не `kind: bootstrap`. Строки шаблона A.9 от принятых гейт не отличает.
    """
    homes = [d for d in ARCHIVE.iterdir() if (d / "STATUS.md").is_file()
             and not d.name.startswith("<")] if ARCHIVE.is_dir() else []
    if all(field(read(d / "STATUS.md"), "kind").lower().startswith("bootstrap") for d in homes):
        return
    readme = read(DELIVERY / "evals" / "smoke" / "README.md")
    rows = len(re.findall(r"(?m)^\s*\|\s*[A-Za-z]+\d+\s*\|", readme))
    print(f"сквозные проверки §6.1: сценариев в таблице {rows} (delivery/evals/smoke/README.md)")
    if not rows:
        warnings.append("delivery/evals/smoke/README.md: таблица сценариев пуста, а код уже "
                        "поставлен — в архиве есть поставка не kind: bootstrap (§6.1: минимум — "
                        "здоровье, тесты, миграции). Обещание строкой README не исполняет никто: "
                        "merge_guard и CI без run.sh шаг пропускают")


def main() -> int:
    args = parse_args()
    # Вне git-репозитория дифф, база и архив не судятся, а «0 error(s)» выходило нулём
    # (`delivery@2.20`).
    if not git("rev-parse", "--git-dir").strip():
        print("delivery_check: вне git-репозитория — судить нечего: это не «нарушений нет» "
              f"(дерево {ROOT})", file=sys.stderr)
        return 1

    errors: list[str] = []
    warnings: list[str] = []

    if not check_preconditions(args, errors, warnings):
        return 1

    check_archive_index(args, errors, warnings)

    if args.require_spec and not (ACTIVE / "spec.md").is_file():
        errors.append("--require-spec: active/spec.md missing")
    if args.require_verify and not (ACTIVE / "verify-report.md").is_file():
        errors.append("--require-verify: verify-report.md missing")

    check_permissions(errors, warnings)

    check_layer_pointers(warnings)
    check_smoke_table(warnings)

    for w in warnings:
        print(f"WARNING: {w}")
    for e in errors:
        print(f"ERROR: {e}", file=sys.stderr)
    print(f"delivery_check: {len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
