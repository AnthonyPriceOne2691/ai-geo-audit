#!/usr/bin/env python3
"""Мёртвый код: объявлено — и никем не зовётся. Ратчет по снимку (`cqg@3.09`).

ruff `F401`/`F841` и `noUnused*` у TypeScript видят импорты и локальные; функцию,
класс, константу, экспорт и файл целиком не искал никто. Половин две, по стеку:
python — vulture (уверенность от 60 %), ts — knip. Обе пишут в ОДИН снимок
`<число>:<ключ>`; ключ — `путь::имя` (файл целиком — `путь`), номера строки в нём
нет: сдвиг кода снимок не трогает. Легаси лежит в снимке и тает, новое — красное.

Использование ищется по ВСЕМ отслеживаемым `.py` вне механики контура, вместе с
тестами: «жив только в тестах» — суждение, а не дефект. Судятся находки под
`LINT_PY_SRC` / `LINT_TS_SRC`, не в тестах и не в миграциях.

Ложное у python снимают правила (`dead_code_rules.py`), у TS — точки входа из конфигов
сборки, а чего knip не выведет — JSON-конфиг проекта (`LINT_KNIP_CONFIG`) под правилом гейта.
Остальное называет проект: `dead-code-allowed.json` рядом, `{"шаблон ключа": "причина"}`;
строка без причины — отказ. Нет инструмента, он упал, ответ не разобран или половина
смотрит мимо кода — «не просудил», красное: это НЕ «мёртвого кода нет». Законный выход —
объявить роль или половину (`check_dead_code.py:python`, `:ts`) в `not-applicable.json`.

  check_dead_code.py             # проверка
  check_dead_code.py --generate  # пере-снять снимок (только вниз, §8.2)
Env: LINT_PY_SRC, LINT_TS_SRC, LINT_FE_DIR, LINT_BE_DIR, LINT_VENV, LINT_KNIP_CONFIG, STRICT=0 — soft.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from fnmatch import fnmatchcase
from pathlib import Path

from dead_code_rules import NotJudgedError, python_dead
from doctor_hooks import HookReaders

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
ME = Path(__file__).name
ENV = os.environ.get
PY_SRC = ENV("LINT_PY_SRC") or "backend/features"
FE_DIR = ENV("LINT_FE_DIR") or "frontend"
TS_SRC = ENV("LINT_TS_SRC") or f"{FE_DIR}/src"
BE_DIR, VENV = ENV("LINT_BE_DIR") or "backend", ENV("LINT_VENV") or ".venv"
BASELINE, ALLOWED, NA = (HERE / n for n in (
    "dead_code_baseline.txt", "dead-code-allowed.json", "not-applicable.json"))
TS_MASKS = ("*.ts", "*.tsx", "*.mts", "*.cts", "*.js", "*.jsx", "*.mjs", "*.cjs")

# «Тестовый файл» — понятие §3.1e, выражение то же, что у `check_ast_gate.py`:
# согласие владельцев держит `tests/test_what_is_a_test_file.py`.
IS_TEST = re.compile(
    r"(^|/)(test|tests|Tests|__tests__|spec|specs)/|(^|/)conftest\.py$|(^|/)test[_-]"
    r"|[_-](test|spec)\.|(Test|Tests|Spec|Specs)\.|\.(test|spec)\.")

VULTURE_RE = re.compile(r"(.+?):(\d+): (?:unused (\w+) '(.+)'|(unreachable) code after '(.+)')"
                        r" \((\d+)% confidence")
# knip судит по своему знаменателю — числу файлов проекта; json-репортёр его не
# печатает, поэтому рядом идёт свой репортёр в одну строку. Нет числа — отказ.
KNIP_SEEN = "export default o => console.log(JSON.stringify({seen: o.counters.total}))\n"
KNIP_KINDS = ("files", "exports", "types", "enumMembers")


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


def tracked(*masks: str) -> list[str]:
    """Отслеживаемые файлы по маскам, как есть (`-z`), вне механики контура."""
    out = subprocess.run(["git", "ls-files", "-z", "--", *masks], cwd=ROOT, env=GIT_ENV,
                         capture_output=True, encoding="utf-8", check=False).stdout
    return [p for p in out.split("\0")
            if p and not HookReaders.CONTOUR_RE.match(p) and (ROOT / p).is_file()]


def judged(files: list[str], src: str) -> list[str]:
    """Население половины: под корнем, не тест, не миграция."""
    src = os.path.normpath(src)
    return [f for f in files if (src == "." or f.startswith(src + "/"))
            and not IS_TEST.search(f) and "migrations" not in f.split("/")]


def tool(name: str, *dirs: str) -> str | None:
    hits = [ROOT / d / name for d in dirs if os.access(ROOT / d / name, os.X_OK)]
    return str(hits[0]) if hits else shutil.which(name)


def unread(what: str, run: subprocess.CompletedProcess) -> NotJudgedError:
    said = (run.stderr or run.stdout).strip()[:300] or "вывод пуст"
    return NotJudgedError(f"{what} вышел с кодом {run.returncode}, и его ответ не читается "
                          f"как находки: {said}")


def python_half(sources: list[str], population: set[str]) -> dict[str, list[str]]:
    exe = tool("vulture", f"{BE_DIR}/{VENV}/bin", f"{VENV}/bin", f"{BE_DIR}/.venv/bin")
    if not exe:
        raise NotJudgedError("python: vulture не найден — ни в venv проекта (LINT_VENV, LINT_BE_DIR), "
                             "ни в PATH. Он в requirements-dev.txt: pip install vulture")
    # `--config`: конфиг проекта не читается — что считать живым, решают правила гейта.
    run = subprocess.run([exe, "--min-confidence", "60", "--config", os.devnull, *sources],
                         cwd=ROOT, capture_output=True, encoding="utf-8", check=False)
    rows = [VULTURE_RE.match(ln) for ln in run.stdout.splitlines() if ln.strip()]
    # stderr: предупреждения python о тексте источников — не отказ, неразобранный файл — отказ.
    errors = [ln for ln in run.stderr.splitlines()
              if ln.strip() and ln[0] != " " and "Warning" not in ln]
    if run.returncode not in (0, 3) or None in rows or (run.returncode == 3) != bool(rows) or errors:
        raise unread("python: vulture", run)
    return python_dead(ROOT, [(m[1], int(m[2]), m[3] or m[5], m[4] or m[6], int(m[7])) for m in rows],
                       [*sources, *tracked("*pyproject.toml")], population)


def knip_answer(stdout: str) -> dict:
    """Две строки JSON — счётчик своего репортёра и находки json-репортёра; иначе пусто."""
    data: dict = {}
    try:
        for line in stdout.splitlines():
            data.update(json.loads(line) if line.strip() else {})
    except (ValueError, TypeError):
        return {}
    return data


def knip_rows(data: dict) -> list[tuple[str, str, int]]:
    """(путь от корня репозитория, ключ снимка, строка) на каждую находку knip."""
    rows = []
    for issue in data.get("issues", []):
        path = os.path.normpath(os.path.join(FE_DIR, issue["file"]))
        for kind in KNIP_KINDS:
            for item in issue.get(kind, []):
                name = ".".join(filter(None, (item.get("namespace"), item["name"])))
                rows.append((path, path if kind == "files" else f"{path}::{name}", item.get("line", 1)))
    return rows


def knip_config(tmp: str) -> str:
    """Правило гейта поверх JSON-конфига проекта (`LINT_KNIP_CONFIG`): knip берёт ОДИН конфиг.
    Экспорт, нужный своему файлу, — не находка: 166 находок против 3 (26.10); G connect 14.11 —
    точка входа CLI своим конфигом без правила дала 92 находки, с правилом 9."""
    own, conf = ENV("LINT_KNIP_CONFIG"), {}
    if own:
        try:
            conf = json.loads((ROOT / own).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            conf = f"{type(exc).__name__}: {exc}"
        if not isinstance(conf, dict):
            raise NotJudgedError(f"ts: LINT_KNIP_CONFIG={own} — не JSON-объект ({str(conf)[:160]}): правило "
                                 "гейта кладётся поверх своего конфига, и конфиг для гейта — JSON (§3.8)")
    Path(tmp, "knip.json").write_text(json.dumps({**conf, "ignoreExportsUsedInFile": True}), encoding="utf-8")
    return f"{tmp}/knip.json"


def ts_half() -> tuple[dict[str, list[str]], int]:
    if not (ROOT / FE_DIR).is_dir():
        raise NotJudgedError(f"ts: каталога фронта {FE_DIR} нет, а код под {TS_SRC} есть — knip идёт из него "
                             "(package.json, node_modules): задай LINT_FE_DIR в entry хука и в env CI (§6)")
    exe = tool("knip", f"{FE_DIR}/node_modules/.bin", "node_modules/.bin")
    if not exe:
        raise NotJudgedError(f"ts: knip не найден — ни в {FE_DIR}/node_modules/.bin, ни в PATH: npm i -D knip")
    with tempfile.TemporaryDirectory(prefix="dead-code-") as tmp:
        Path(tmp, "seen.mjs").write_text(KNIP_SEEN, encoding="utf-8")
        run = subprocess.run(
            [exe, "--config", knip_config(tmp), "--include", ",".join(KNIP_KINDS), "--reporter",
             f"{tmp}/seen.mjs", "--reporter", "json", "--no-progress", "--no-config-hints"],
            cwd=ROOT / FE_DIR, capture_output=True, encoding="utf-8", check=False)
    data = knip_answer(run.stdout)
    rows = knip_rows(data)
    if run.returncode not in (0, 1) or not {"seen", "issues"} <= set(data) or (run.returncode == 1) != bool(rows):
        raise unread("ts: knip", run)
    found: dict[str, list[str]] = {}
    for path, key, line in rows:
        if judged([path], TS_SRC):
            found.setdefault(key, []).append(f"{path}:{line}")
    return found, int(data["seen"])


def declared(path: Path) -> dict:
    """Объявление проекта `{ключ: …}`; ключ с `_` — комментарий (§5.5)."""
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {k: v for k, v in raw.items() if not k.startswith("_")}
    except (ValueError, AttributeError) as exc:
        raise NotJudgedError(f"{path.name} не разобран как объект {{ключ: причина}} ({exc})") from exc


def half(lang: str, files: list[str], src: str, na: dict, notes: list[str]) -> list[str]:
    """Население половины; пусто — пропуск НАЗВАН, а «смотрит мимо кода» — отказ."""
    if f"{ME}:{lang}" in na:
        notes.append(f"{lang}: объявлена неприменимой в {NA.name}")
        return []
    population = judged(files, src)
    elsewhere = [f for f in files if not IS_TEST.search(f) and not f.startswith("scripts/")]
    if not population and elsewhere:
        raise NotJudgedError(
            f"{lang}: под {src} кода нет, а в репозитории он есть — {len(elsewhere)} файл(ов), первый "
            f"{elsewhere[0]}. Половина смотрит мимо: задай LINT_{'PY' if lang == 'python' else 'TS'}_SRC "
            f"в entry хука и в env CI (§6) либо объяви «{ME}:{lang}» в {NA.name} с причиной")
    if not population:
        notes.append(f"{lang}: кода этого стека вне scripts/ и тестов в репозитории нет")
    return population


def survey(na: dict) -> tuple[dict[str, list[str]], int, list[str], list[str]]:
    """Обе половины: (находки, просмотрено, маски судивших половин, названные пропуски)."""
    found: dict[str, list[str]] = {}
    seen, masks, notes = 0, [], []
    # Вне git-репозитория обе половины «судить нечего» выходили нулём (`cqg@3.13`).
    if subprocess.run(["git", "rev-parse", "--git-dir"], cwd=ROOT, env=GIT_ENV,
                      capture_output=True, check=False).returncode:
        raise NotJudgedError(f"вне git-репозитория — судить нечего: это не «нарушений нет» "
                             f"(дерево {ROOT})")
    sources = tracked("*.py")
    population = half("python", sources, PY_SRC, na, notes)
    if population:
        found.update(python_half(sources, set(population)))
        seen += len(sources)
        masks.append(f"{PY_SRC}/**/*.py")
    if half("ts", tracked(*TS_MASKS), TS_SRC, na, notes):
        ts_found, ts_seen = ts_half()
        found.update(ts_found)
        seen += ts_seen
        masks.append(f"{TS_SRC}/**")
    return found, seen, masks, notes


def allow(found: dict[str, list[str]]) -> tuple[dict[str, list[str]], list[str]]:
    """Белый список проекта: (остаток, строки, не снявшие ничего)."""
    allowed = declared(ALLOWED)
    bare = sorted(k for k, v in allowed.items() if not str(v).strip())
    if bare:
        raise NotJudgedError(f"{ALLOWED.name}: строка без причины — {', '.join(bare[:5])}. "
                             "Исключение без причины исключением не является")
    hit = {key: next((p for p in allowed if fnmatchcase(key, p)), None) for key in found}
    return ({k: v for k, v in found.items() if hit[k] is None},
            sorted(set(allowed) - set(hit.values())))


def generate(found: dict[str, list[str]], seen: int, masks: list[str]) -> int:
    head = ["# dead_code_baseline.txt — снимок гейта мёртвого кода. Генерируется --generate, НЕ руками.",
            "# Формат: <число>:<путь>::<имя> (файл целиком — <число>:<путь>). По маске: " + " ".join(masks),
            "# Снимок только ТАЕТ: удалил мёртвое — пере-сними вниз. Новое мёртвое — красное."]
    BASELINE.write_text("\n".join(head + [f"{len(v)}:{k}" for k, v in sorted(found.items())]) + "\n",
                        encoding="utf-8")
    print(f"baseline пересобран: {BASELINE.name} — просмотрено {seen} файл(ов), находок {len(found)}")
    return 0


def baseline() -> dict[str, int]:
    snap: dict[str, int] = {}
    for line in BASELINE.read_text(encoding="utf-8").splitlines() if BASELINE.is_file() else ():
        count, _, key = line.partition(":")
        if count.isdigit() and key:
            snap[key] = int(count)
    return snap


def judge(found: dict[str, list[str]], seen: int, masks: list[str], strict: bool) -> int:
    snap = baseline()
    new = {k: v for k, v in sorted(found.items()) if len(v) > snap.get(k, 0)}
    if not new:
        print(f"dead-code: OK — просмотрено {seen} файл(ов), находок {len(found)}, все в снимке "
              f"({len(snap)}), по маске: {' '.join(masks)}")
        return 0
    mark, word = ("✗", "ERROR") if strict else ("⚠", "WARNING")
    for key, where in new.items():
        print(f"  {mark}  {key} — {', '.join(where)} (в снимке {snap.get(key, 0)})")
    print(f"\n{word}: мёртвый код вне снимка — {len(new)} объявлени(й) никто не зовёт.")
    print(f"Удали. Зовут не по имени (строкой, конфигом, извне) — назови в scripts/lint/{ALLOWED.name}: "
          '{"ключ": "причина"}.')
    print(f"Легаси впервые под гейтом — python3 scripts/lint/{ME} --generate (снимок только вниз).")
    return 1 if strict else 0


def main() -> int:
    strict = ENV("STRICT", "1") != "0"
    if sys.argv[1:] not in ([], ["--generate"]):
        print(f"usage: {ME} [--generate]", file=sys.stderr)
        return 2
    try:
        na = declared(NA)
        if ME in na:
            print(f"⚠ dead-code объявлен неприменимым в {NA.name} — пропуск НАЗВАН, прогона не было.")
            return 0
        found, seen, masks, notes = survey(na)
        found, stale = allow(found)
    except NotJudgedError as exc:
        print(f"✗ dead-code: {exc}\nГейт НЕ ПРОСУДИЛ — это не «мёртвого кода нет».")
        if not strict:
            print("WARNING (STRICT=0)")
        return 1 if strict else 0
    for note in notes:
        print(f"  ○ половина не судилась — {note}")
    for pattern in stale:
        print(f"  ○ {ALLOWED.name}: «{pattern}» не снимает ни одной находки — строка устарела, убери")
    if not masks:
        print("⚠ dead-code пропущен — судить нечего: обе половины пропущены (причины выше)")
        return 0
    return generate(found, seen, masks) if sys.argv[1:] else judge(found, seen, masks, strict)


if __name__ == "__main__":
    sys.exit(main())
