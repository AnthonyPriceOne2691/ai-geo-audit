#!/usr/bin/env python3
"""Общее для всех частей доктора: вердикты, палитра, запуск подпроцесса.

Доктор разрезан на модули (`cqg@1.82`): он вырос до 1082 строк при планке 300
(Delivery §9.1a п.5), и рос быстрее, чем проверяемое им, — это назвал полевой
отчёт раньше, чем ратчет веса. Здесь то, что нужно ВСЕМ частям, и только оно:
модуль не импортирует соседей, поэтому цикла быть не может по построению.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

# --- вердикты ----------------------------------------------------------------
AUTO = "AUTO"      # проверено исполнением: канарейка покраснела
WEAK = "WEAK"      # работает, но не судит; причина названа самим гейтом
DEAD = "DEAD"      # объявлен — и молчит на своей канарейке или не стартует. Только это ложь
ABSENT = "ABSENT"  # не развёрнут
SKIP = "SKIP"      # доктор не умеет это пробовать — назвать, а не умолчать
TOOL = "TOOL"      # это не гейт, а измерительный инструмент: судить его нечем

ORDER = {DEAD: 0, SKIP: 1, WEAK: 2, ABSENT: 3, TOOL: 4, AUTO: 5}
COLOR = {AUTO: "\033[32m", WEAK: "\033[33m", DEAD: "\033[31m",
         ABSENT: "\033[90m", SKIP: "\033[36m", TOOL: "\033[90m"}
RESET = "\033[0m"

NOT_GATES = {"check_gate_value.sh", "assert_digest.sh", "contour_doctor.py"}
#: Четыре канона — здесь, а не в `doctor_versions`: снимок судят и версии, и состав (`cqg@2.99`).
CANONS = ("AGENT_STACK.md", "AGENT_DELIVERY_HARNESS.md",
          "CODE_QUALITY_GATES.md", "OKF_KNOWLEDGE_BUNDLE.md")

SKIP_WORDS = ("пропущен", "не судит", "не проверен", "нет каталога", "ПРОПУЩЕНА",
              "WARNING", "не найден", "недоступен", "не смог")
SUCCESS_WORDS = (": OK", "OK —", "OK -")

#: Гейт НЕ СТАРТОВАЛ — не вердикт, и красный код тут не поимка (`cqg@2.79`): вход без
#: части отказывает фразой `cqg@2.75` (shell) либо падает на импорте (python). Импорт
#: читается с начала строки и только у python-входа: трасса инструмента, которого
#: позвал shell-гейт, — не отказ самого гейта.
REFUSED_RE = re.compile(r"(часть \S+ не развёрнута) в .+ — гейт не стартует")
IMPORT_RE = re.compile(r"(?m)^((?:ModuleNotFoundError|ImportError): [^(\n]+)")
#: Вход старше `cqg@2.75` сверки не несёт: `. "$SCRIPT_DIR/часть.sh"` без файла — сырая
#: ошибка шелла, и вход идёт дальше (`cqg@2.88`). Отказом она читается только о части,
#: которую гейт зовёт из СВОЕГО каталога (`PART_SH`): о чужом файле — это сбой среды.
RAW_SOURCE_RE = re.compile(r"(?m): line \d+: (?:.*/)?([\w.-]+\.sh): No such file or directory$")
#: Часть гейта рядом с ним: `. "$SCRIPT_DIR/x.sh"` у shell (`from x import` — у python).
PART_SH = re.compile(r'\$\{?SCRIPT_DIR\}?/([A-Za-z0-9_]+\.sh)')
#: Окружения проекта — те же, где доктор ищет инструменты (`check_tools`).
PY_ENVS = (".venv", "backend/.venv")
MODULE_RE = re.compile(r"No module named '([\w.]+)'")


def _block_after(text: str, marker: str, lang: str | None) -> str | None:
    """Первый блок ```lang после marker — с учётом ВЛОЖЕННЫХ фенсов.

    Наивная регулярка обрывается на первом же вложенном фенсе (шаблоны канона их
    содержат), и сравнение тел давало бы вечное «разошлось». Тот же построчный
    сканер, что blocks() в stack_selftest и block_after в сьюте канона.

    `lang=None` — блок ЛЮБОГО языка: строгий `lang` чужой блок не отвергает, а
    проскакивает (`"yaml"` у `eslint.config.js` давал `.pre-commit-config.yaml`).
    Маркер — с начала строки: подстрокой он находился в докстринге `canon_configs`.
    """
    hit = re.search(r"(?m)^" + re.escape(marker), text)
    if hit is None:
        return None
    opener = re.compile(re.escape(lang) if lang else r".+")
    out, depth, started = [], 0, False
    for line in text[hit.end():].splitlines():
        if line.startswith("```"):
            info = line[3:].strip().lower()
            if not started:
                if opener.fullmatch(info):
                    started, depth = True, 1
                continue
            if info:
                depth += 1
            else:
                depth -= 1
                if depth == 0:
                    return "\n".join(out)
            out.append(line)
            continue
        if started:
            out.append(line)
    return None


def run(cmd: list[str], cwd: Path, env: dict | None = None) -> tuple[int, str]:
    # Привязка git из окружения снята у каждого запуска (§6, `cqg@3.12`): под хуком
    # `git init` и `commit` полигонов проб уходили в репозиторий вызвавшего.
    e = {k: v for k, v in os.environ.items()
         if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}
    e.update(env or {})
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                           timeout=180, env=e)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return 124, f"{exc}"
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def tracked(root: Path) -> list[str]:
    """Отслеживаемые пути как есть — одно место на чтения путей доктора (`cqg@3.01`).

    Без `-z` кириллицу git отдаёт в кавычках и восьмеричных кодах: такой путь не кончается
    ни расширением, ни именем манифеста — доктор не видел его ни в знаменателе области,
    ни в признаках модели и стека, ни среди копий едущих файлов. Нет git — пусто.
    """
    code, out = run(["git", "ls-files", "-z"], root)
    return [p for p in out.split("\0") if p] if code == 0 else []


def not_started(script: Path, out: str) -> str | None:
    """Почему гейт не стартовал — часть либо импорт; None — стартовал и судил."""
    m = REFUSED_RE.search(out) or (script.suffix == ".py" and IMPORT_RE.search(out))
    if m:
        return m.group(1).strip()
    if script.suffix == ".sh" and RAW_SOURCE_RE.search(out):
        own = set(PART_SH.findall(script.read_text(encoding="utf-8", errors="replace")))
        for name in RAW_SOURCE_RE.findall(out):
            if name in own:
                return f"часть {name} не развёрнута"
    return None


def library_env(root: Path, why: str) -> str | None:
    """Окружение проекта, где модуль из отказа импорта ЕСТЬ, — тогда это библиотека
    гейта, а не пропавшая часть (`cqg@2.89`); None — модуля нет и там."""
    m = MODULE_RE.search(why)
    if not m:
        return None
    probe = "import importlib.util as u, sys; sys.exit(u.find_spec(sys.argv[1]) is None)"
    for env in PY_ENVS:
        py = root / env / "bin" / "python"
        if py.is_file() and run([str(py), "-c", probe, m.group(1).split(".")[0]], root)[0] == 0:
            return env
    return None


