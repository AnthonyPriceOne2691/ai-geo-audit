#!/usr/bin/env python3
"""Delivery metrics collector (§9 / A.10).

Usage:
  python scripts/delivery_metrics.py --base origin/main
  python scripts/delivery_metrics.py --base origin/main --write

--write вставляет/обновляет блок "## Harness metrics" в
delivery/active/verify-report.md. Exit 0 всегда: это отчёт, не гейт
(гейт на наличие блока — в delivery_check.py на фазе handoff). Кроме дерева вне
git-репозитория и базы без окна: отчёта там нет, и это отказ, а не ноль (`delivery@2.20`,
`delivery@2.23`).
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTIVE = ROOT / "delivery" / "active"
STATUS = ACTIVE / "STATUS.md"
VERIFY = ACTIVE / "verify-report.md"

# Правка любого из этих путей = harness усилен (§5.4): новый oracle / hook / eval.
HARDENING_PATHS = (
    "scripts/lint/",
    "delivery/evals/",
    ".pre-commit-config.yaml",
    ".github/workflows/",
    # Оба хостинга: у контура гейт мержа чисто гитовый, а CI-контракт §10.4
    # описан шагами, поэтому путь конфига — свойство хостинга, не канона.
    ".gitlab-ci.yml",
    "scripts/delivery_check.py",
    "scripts/okf_sync_gate.py",
    # `.claude/` — механика контура (права агента §4.5), а не продуктовый код.
    # Без исключения маячок §12.5 срабатывал на классе «безопасность» из-за
    # слова `permission` в собственном файле настроек прав: контур ругался на
    # свою же машинерию. Найдено на первом прогоне маячка (арена lab-10).
    ".claude/",
)
# НОВЫЙ тест-файл — тоже усиление harness: по словарю §1.1 oracle это «гейты + тесты
# + smoke + фазовые артефакты», и §5.4 считает усилением «добавить oracle». Путей
# тестов в списке не было, поэтому поставка, добавившая два регрессионных оракула
# (включая мета-оракул «проверка, что защита подключена»), получала
# `harness_hardened: no` — а §9.2 по этой метрике советует «добавь oracle», то есть
# добавить уже добавленное. Нашло приёмочное развёртывание.
#
# Считаются только ДОБАВЛЕННЫЕ файлы (`--diff-filter=A`), а не любая правка тестов:
# иначе метрика стала бы всегда `yes` — тесты трогает каждая поставка — и перестала
# бы отвечать на свой вопрос.
# ⚠ Признак — ТОТ ЖЕ, что у §3.1e CQG, и это не совпадение, а одно понятие:
# подстроки промахивались на Maven (`src/test/java/FooTest.java`), Jest
# (`__tests__/foo.tsx`) и xUnit (`FooTests.cs`), то есть метрика снова
# советовала «добавь oracle» тому, кто его добавил, — уже второй раз.
# Согласие с четырьмя реализациями CQG держит `tests/test_what_is_a_test_file.py`.
TEST_PATH_RE = re.compile(
    r"(^|/)(test|tests|Tests|__tests__|spec|specs)/|(^|/)conftest\.py$|(^|/)test[_-]"
    r"|[_-](test|spec)\.|(Test|Tests|Spec|Specs)\.|\.(test|spec)\.")
# «code» метрики — то, что меряют breaker'ы §3.4 (`delivery@2.25`): исключения — копия
# `delivery_base.py` (Приложение C разворачивается без гейта), согласие держит
# `tests/test_breaker_scope.py`. Метрика брала одни документы поставки, и у первого
# проекта на `delivery@2.21` `backend/uv.lock` дал ей +86/−2 сверх числа гейта.
BREAKER_EXCLUDE = (
    "delivery/", "knowledge/", "scripts/lint/", "scripts/merge_guard.sh", "scripts/delivery_",
    "scripts/okf_", ".claude/", "docs/canon/", ".pre-commit-config.yaml", ".github/workflows/",
    ".gitlab-ci.yml", "scripts/check_irreversible_signature.sh", "scripts/sign_irreversible.sh",
    "stack_selftest.py", "selftest_sizes.py", "extract_payload.py",
    "scripts/stack_selftest.py", "scripts/selftest_sizes.py", "scripts/extract_payload.py")
GENERATED_FILENAMES = (
    "package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb",
    "poetry.lock", "uv.lock", "Pipfile.lock", "Cargo.lock", "go.sum", "Gemfile.lock",
    "composer.lock", "Podfile.lock")
CONTOUR_CONFIGS = (".dependency-cruiser.cjs", "eslint.config.js", "eslint.config.mjs",
                   "eslint.config.cjs")


def out_of_blast_radius(path: str) -> bool:
    """Путь вне breaker'ов §3.4 — тот же ответ, что у гейта."""
    name = path.rsplit("/", 1)[-1]
    return (path.startswith(BREAKER_EXCLUDE) or name in GENERATED_FILENAMES
            or name in CONTOUR_CONFIGS)


def tree_env(root: Path) -> dict[str, str]:
    """Окружение git для дерева гейта (CQG §6): адрес репозитория из окружения снят, индекс
    остаётся только свой — привязку мог выдать хук другого дерева."""
    env = {k: v for k, v in os.environ.items() if k not in ("GIT_DIR", "GIT_WORK_TREE")}
    index = env.get("GIT_INDEX_FILE", "")
    try:
        own = os.path.isabs(index) and subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--absolute-git-dir"], env=env,
            capture_output=True, text=True, check=False).stdout.strip()
        mine = bool(own) and os.path.samefile(os.path.dirname(index), own)
    except OSError:
        mine = False
    if not mine:
        env.pop("GIT_INDEX_FILE", None)
    return env


GIT_ENV = tree_env(ROOT)


def git(*args: str) -> str:
    try:
        out = subprocess.run(["git", *args], cwd=ROOT, env=GIT_ENV,
                             capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return ""
    return out.stdout if out.returncode == 0 else ""


def resolves(ref: str) -> bool:
    return bool(ref) and bool(git("rev-parse", "--verify", "--quiet", ref).strip())


def window_refusal(base: str) -> str:
    """Почему у базы нет окна, с подсказкой, либо "" — как `delivery_base.window_refusal`
    (`delivery@2.23`): части гейта отчёт не импортирует, согласие держит прогон.

    Без окна в verify-report уходил блок «(collection failed)», и handoff его принимал."""
    if resolves(base) and git("merge-base", base, "HEAD").strip():
        return ""
    if not base:
        return "база '' пуста — переменная базы до вызова не дошла"
    if resolves(base):
        return f"база '{base}' не имеет общей истории с HEAD (неглубокий клон?)"
    alt = base[len("origin/"):] if base.startswith("origin/") else f"origin/{base}"
    hint = (f"; есть '{alt}' — передай --base {alt}"
            if re.fullmatch(r"[\w./-]+", base) and resolves(alt) else "")
    return f"база '{base}' в этом клоне не разрешается{hint}"


def numstat(span: str, *flags: str) -> list[tuple[str, str, str]]:
    """(путь, добавлено, удалено) из `git diff --numstat -z` — пути как есть (`delivery@2.13`).

    Без `-z` кириллический путь приходил в кавычках, переименование — строкой
    `{старое => новое}`: `delivery/заметка.md` шла кодом, а гейт в `scripts/lint/` и новый
    `tests/проверка.py` усилением не считались. Разбор — тот же, что `delivery_diff.numstat`:
    скрипт метрик частей гейта не импортирует, согласие держит прогон на одном диффе.
    """
    parts = git("diff", "--numstat", "-z", *flags, span).split("\0")
    out: list[tuple[str, str, str]] = []
    i = 0
    while i < len(parts):
        fields = parts[i].split("\t", 2)
        if len(fields) != 3:
            i += 1
            continue
        a, d, path = fields
        if path:
            i += 1
        else:
            path, i = (parts[i + 2] if i + 2 < len(parts) else ""), i + 3
        if path:
            out.append((path, a, d))
    return out


def first_commit(
    path: str, needle: str | None = None, rev_range: str | None = None
) -> tuple[str, datetime] | None:
    """Первый коммит, добавивший path (или впервые внёсший needle в path).

    needle — РЕГУЛЯРКА (pickaxe-regex), а не литерал: в STATUS.md поля размечены
    markdown-жирным (`**phase:** handoff`), поэтому поиск литерала "phase: handoff"
    не находил ничего и метрики молча выходили пустыми.

    ⚠ rev_range ОБЯЗАТЕЛЕН для путей внутри `delivery/active/`, и это не
    перестраховка. Путь **переиспользуется каждой поставкой**: `STATUS.md` и
    `spec.md` у всех одни и те же, поэтому «первый в истории» — это первая
    поставка репозитория, а не текущая. lab-12 (нашли ОБЕ арки независимо):
    `rework_after_done` печатал «3 commit(s) after first phase: handoff» на
    поставке, которая handoff не объявляла — счёт шёл от handoff'а bootstrap'а.
    Собственная проба нашла и брата, которого не увидел никто:
    `time_to_accepted_spec` выдавал `4.0d` там, где спека и подпись сделаны в
    один день, — число из ПРОШЛОЙ поставки, и оно выглядит правдоподобно, потому
    что это настоящее число, просто не про эту работу.

    Класс: **переиспользуемый путь + поиск по всей истории = чужой ответ с видом
    своего.** Родня `--diff-filter=A` тому же: «файл добавлен» тоже случилось один
    раз, в первой поставке.
    """
    args = ["log", "--reverse", "--format=%H %aI"]
    if rev_range:
        args.append(rev_range)
    if needle:
        args += [f"-S{needle}", "--pickaxe-regex"]
    else:
        args.append("--diff-filter=A")
    args += ["--", path]
    for line in git(*args).splitlines():
        sha, _, iso = line.partition(" ")
        if sha and iso:
            try:
                return sha, datetime.fromisoformat(iso.strip())
            except ValueError:
                return None
    return None


def hours_between(a: datetime, b: datetime) -> str:
    delta = (b - a).total_seconds() / 3600
    return f"{delta:.1f}h" if delta < 48 else f"{delta / 24:.1f}d"


def _diff_stats(span: str) -> tuple[str, list[str]]:
    """(строка `files_touched / loc_diff`, пути усиления) за один проход по diff'у.

    Шов по данным: наружу блок отдаёт ровно две величины — готовую строку метрики
    и список путей усиления; четыре счётчика за границей мертвы. Здесь же треть
    ветвлений `collect` (было 69/20).
    """
    code_files = excluded = added = deleted = 0
    hardened: list[str] = []
    for path, a, d in numstat(span):
        if path.startswith(HARDENING_PATHS):
            hardened.append(path)
        if out_of_blast_radius(path):
            excluded += 1
            continue
        code_files += 1
        added += int(a) if a.isdigit() else 0
        deleted += int(d) if d.isdigit() else 0
    return (
        f"{code_files} code (+{excluded} outside breakers §3.4) / "
        f"+{added}/-{deleted} (net {added - deleted:+d})",
        hardened,
    )


def _spec_timings(span: str) -> dict[str, str]:
    """Две метрики §9, обе — про смену фазы в истории `STATUS.md`.

    Шов по данным: сверху блок читает один `span`, вниз отдаёт две готовые
    строки — `spec_created`/`spec_ok`/`handoff` дальше не читает никто. Обе
    величины меряют одно и то же событие, поэтому и режутся вместе.
    """
    spec_created = first_commit("delivery/active/spec.md", rev_range=span)
    spec_ok = first_commit(
        "delivery/active/STATUS.md", r"human_ok_spec:\**[ \t]*yes", rev_range=span
    )
    if spec_created and spec_ok:
        accepted = hours_between(spec_created[1], spec_ok[1])
    elif spec_created:
        accepted = "spec drafted, not yet accepted"
    else:
        accepted = "n/a (no spec.md in history — class S?)"

    handoff = first_commit(
        "delivery/active/STATUS.md", r"phase:\**[ \t]*handoff", rev_range=span
    )
    if handoff:
        after = git("rev-list", "--count", f"{handoff[0]}..HEAD").strip() or "0"
        rework = f"{after} commit(s) after first phase: handoff"
    else:
        rework = "0 (handoff not declared yet)"
    return {"time_to_accepted_spec": accepted, "rework_after_done": rework}


def _hardened_line(span: str, hardened: list[str]) -> str:
    """Значение `harness_hardened`: пути из диффа плюс новые файлы-оракулы.

    Шов по данным: сюда входит список из `_diff_stats`, отсюда выходит строка
    метрики — пополненный список за границей не читает никто.
    """
    # Новые тест-файлы — отдельный источник усиления (см. TEST_PATH_RE).
    found = list(hardened)
    for path, _, _ in numstat(span, "--diff-filter=A"):
        if TEST_PATH_RE.search(path):
            found.append(f"{path} (новый оракул)")
    return f"yes — {', '.join(sorted(set(found))[:4])}" if found else "no"


def collect(base: str) -> dict[str, str]:
    m: dict[str, str] = {}
    merge_base = git("merge-base", base, "HEAD").strip()

    # Область — ТЕКУЩАЯ поставка, а не вся история: пути в `delivery/active/`
    # переиспользуются, и «первый в истории» отвечает про чужую работу (см.
    # docstring `first_commit`). §5.1 требует ветку на поставку, поэтому
    # `merge_base..HEAD` и есть её граница.
    span = f"{merge_base}..HEAD"

    m["files_touched / loc_diff"], hardened = _diff_stats(span)
    m["commits"] = git("rev-list", "--count", span).strip() or "0"
    m.update(_spec_timings(span))
    m["harness_hardened"] = _hardened_line(span, hardened)
    m["implement_retries"] = "MANUAL — fills from session log"
    m["verify_fails_before_green"] = "MANUAL — count red verify runs (CI run list)"
    m["est_token_or_cost"] = "MANUAL / n/a"
    return m


def render(m: dict[str, str], base: str) -> str:
    rows = "\n".join(f"| {k} | {v} |" for k, v in m.items())
    return (
        "## Harness metrics (this shipment)\n\n"
        f"<!-- generated by scripts/delivery_metrics.py --base {base} -->\n\n"
        "| Metric | Value |\n|---|---|\n"
        f"{rows}\n\n"
        "MANUAL-поля заполняет агент/человек на handoff. Если "
        "`verify_fails_before_green >= 2` при `harness_hardened: no` — по §9.2 "
        "добавь oracle/breaker/hook в этой же поставке.\n"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--write", action="store_true", help="upsert block into verify-report.md")
    args = ap.parse_args()

    # Вне git-репозитория отчёта нет: «(collection failed) … shallow clone?» выходило
    # нулём (`delivery@2.20`).
    if not git("rev-parse", "--git-dir").strip():
        print("delivery_metrics: вне git-репозитория — судить нечего: это не «нарушений нет» "
              f"(дерево {ROOT})", file=sys.stderr)
        return 1
    why = window_refusal(args.base)
    if why:
        print(f"delivery_metrics: окно не определено: {why} — отчёта нет: это не «поставка "
              "пустая»", file=sys.stderr)
        return 1
    block = render(collect(args.base), args.base)

    if not args.write:
        print(block)
        return 0

    if not ACTIVE.is_dir():
        print("no delivery/active/ — nothing to write", file=sys.stderr)
        return 0
    text = VERIFY.read_text(encoding="utf-8") if VERIFY.is_file() else "# Verify report\n\n"
    # Заменить существующий блок целиком, чтобы не копить дубли на повторных прогонах.
    pattern = re.compile(r"(?ms)^## Harness metrics.*?(?=^## |\Z)")
    text = pattern.sub("", text).rstrip() + "\n\n" + block
    VERIFY.write_text(text, encoding="utf-8")
    print(f"metrics written to {VERIFY.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
