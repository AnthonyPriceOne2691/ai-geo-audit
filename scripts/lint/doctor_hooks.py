#!/usr/bin/env python3
"""Чтение `.pre-commit-config.yaml`: чем гейт настроен и когда запускается.

Часть доктора (`cqg@1.88`, вход — `contour_doctor.py`). Выделено из
`doctor_layout.py`, когда тот перевалил за планку 300 строк — journal веса поймал
это в том же прогоне, где строки добавились (§9.1a п.5).

Здесь три вопроса к конфигу, и все три — про НАСТРОЙКУ, а не про суждение:
значения `LINT_*` из `entry:`, объявленная область `files:` и стадии `stages:`.
"""

from __future__ import annotations

import re

#: Стадии pre-commit 4.6 (`clientlib.STAGES`) и старые имена, которые он переводит сам
#: (`transform_stage`). Хук без своих стадий или со `stages: []` получает `default_stages:`
#: файла, а без него — ВСЕ стадии, `manual` тоже (`repository._hook`).
STAGES = ("commit-msg", "post-checkout", "post-commit", "post-merge", "post-rewrite",
          "pre-commit", "pre-merge-commit", "pre-push", "pre-rebase", "prepare-commit-msg",
          "manual")
OLD_STAGES = {"commit": "pre-commit", "merge-commit": "pre-merge-commit", "push": "pre-push"}

#: Значение `LINT_*` — слово shell (`cqg@3.22`): кавычки снимаются, `;` `&` `|` `<` `>` и пробел
#: вне кавычек его кончают. `\S+` брал форму шапки самого канона `export LINT_PY_SRC=backend/app;
#: cd …` вместе с «;» — гейт гонялся в `backend/app;`, и доктор писал DEAD «настроен на
#: НЕСУЩЕСТВУЮЩИЙ путь» (первый проект на `cqg@3.14` обходил это кавычками).
LINT_WORD = re.compile(r"\b(LINT_[A-Z_]+)=((?:\"[^\"]*\"|'[^']*'|[^\s;&|<>\"'])+)")
QUOTED = re.compile(r"\"([^\"]*)\"|'([^']*)'")


def _value_of(lines: list[str], at: int) -> list[str]:
    """Строки значения ключа из строки `at` без комментариев: её остаток, строки глубже ключа
    и пункты `- ` на его же отступе — всё, что YAML отнесёт к значению."""
    ind = len(lines[at]) - len(lines[at].lstrip())
    parts = [lines[at].split(":", 1)[1]]
    for line in lines[at + 1:]:
        d, s = len(line) - len(line.lstrip()), line.strip()
        if s and not s.startswith("#") and (d < ind or d == ind and not s.startswith("-")):
            break
        parts.append(s)
    return [re.sub(r"(^|\s)#.*", "", p).strip() for p in parts]


def stage_list(text: str, key: str = "stages") -> list[str] | None:
    """Стадии под первым ключом `key:` — так, как их прочтёт YAML pre-commit; None — ключа нет.

    Формы — население, которое он принимает (`cqg@3.10`): `[a, b]`, пункты `- a` столбиком
    с отступом и без (так пишет списки `pre-commit sample-config`), кавычки, комментарии в
    строке, между пунктами и на строке ключа. Первая редакция знала одну форму — список в
    строку, — а столбик брала первой строкой с дефисом: `- pre-commit` не был коммитом, и
    проба области молча пропускалась.
    """
    lines = text.splitlines()
    at = next((i for i, l in enumerate(lines) if re.match(rf"\s*{key}:", l)), None)
    if at is None:
        return None
    parts = _value_of(lines, at)
    flow = " ".join(parts).strip()
    items = (flow[1:].split("]")[0].split(",") if flow.startswith("[")
             else [p[1:] for p in parts if p.startswith("-")])
    names = (s.strip().strip("'\"") for s in items)
    return [OLD_STAGES.get(s, s) for s in names if s]


class HookReaders:
    def _hook_env(self, text: str, name: str) -> dict:
        """`LINT_*` из строк `entry:`, где зовётся этот скрипт.

        Значения живут в `entry:` (§6), и гонять гейт без них значило бы мерить
        не тот путь — то есть выдать ложную слепоту. Заодно это проверка самой
        §6: переменной нет в entry — гейт и на коммите работает по дефолту.
        """
        env = {}
        for line in text.splitlines():
            # ⚠ КОММЕНТАРИИ пропускаются, и это не микрооптимизация. Шапка
            # поставляемого `.pre-commit-config.yaml` объясняет, как задавать
            # переменные, ПРИМЕРОМ: `… LINT_PY_SRC=backend/app bash …`. Первая
            # редакция читала его как настоящую настройку и объявляла гейт
            # «настроенным на несуществующий путь» — то есть проверка против
            # ложного зелёного сама давала ложное красное, прочитав пояснение к
            # себе же. Четвёртый рецидив класса, ради которого в сьюте канона
            # живёт `extract.code_only()` (cqg@1.59).
            if line.lstrip().startswith("#") or name not in line:
                continue
            for m in LINT_WORD.finditer(line):
                env[m.group(1)] = QUOTED.sub(r"\1\2", m.group(2))
        return env

    def hook_env_of(self, name: str) -> dict:
        """`LINT_*` хука гейта `name` из конфига — им же пробуется своя канарейка (`cqg@3.22`)."""
        cfg = self.root / ".pre-commit-config.yaml"
        return self._hook_env(cfg.read_text("utf-8", errors="replace"), name) \
            if cfg.is_file() else {}

    #: Расширения, по которым видно, что в репозитории вообще есть исходники.
    SOURCE_EXT = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".astro",
                  ".vue", ".svelte", ".swift", ".go", ".rs", ".java", ".kt",
                  ".rb", ".php", ".cs", ".c", ".cc", ".cpp", ".m", ".mm")

    #: Механика контура из дерева исключается ТЕМ ЖЕ правилом, каким её исключает
    #: сам гейт длины (`exclude_contour`, §9.1a: размер скриптов контура мерится
    #: отдельно). Без этого знаменатель раздували бы скрипты канона, «частичная
    #: область» печаталась бы на КАЖДОМ правильно развёрнутом проекте — а шумную
    #: проверку снимают за неделю (§4.3b), и тогда она не защищает никого.
    #:
    #: ⚠ КОНФИГИ контура — тоже его механика, и это стоило ложного красного
    #: (`cqg@2.04`). Замер: разложи все девять файлов §5.0 — и доктор печатает
    #: `DEAD область check_file_length.sh — маска гейта не покрывает 2 файл(ов)
    #: области: .dependency-cruiser.cjs, frontend/eslint.config.js`, то есть
    #: КАНОННАЯ раскладка обвиняет сама себя. Расширение у них исходное
    #: (`.cjs`/`.js` в `SOURCE_EXT`), поэтому они попадали в знаменатель, а маска
    #: гейта длины `*.py *.ts *.tsx` их не покрывает — и не должна: это конфиги, а
    #: не продуктовый код, и их размер живёт под ратчетом веса канона.
    #: Регрессия развёртывания видела один конфиг из девяти, поэтому сьют молчал.
    CONTOUR_RE = re.compile(r"^(scripts/lint/|scripts/delivery_[a-z_]+\.py$"
                            r"|scripts/okf_[a-z_]+\.py$|scripts/merge_guard\.sh$"
                            r"|scripts/(check_irreversible_signature|sign_irreversible)\.sh$"
                            r"|(scripts/)?(stack_selftest|selftest_sizes|extract_payload)\.py$"
                            r"|delivery/|knowledge/|docs/canon/"
                            r"|([^/]+/)*(\.dependency-cruiser|eslint\.config)\.[cm]?js$)")


    def _hooks(self, text: str) -> list[tuple[str, str]]:
        """[(id, тело)] хуков конфига: тело — до следующего `- id:`, без строк-комментариев.

        Одна нарезка у пробы области, бюджета pre-push и прибора §9.1a (`cqg@3.10`).
        """
        bare = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
        return [(c.split("\n", 1)[0].strip().strip("'\""), c)
                for c in re.split(r"^\s*-\s+id:", bare, flags=re.M)[1:]]

    def _hook_stages(self, text: str, body: str) -> list[str]:
        """Стадии хука с телом `body` в конфиге `text`: на чём он реально запускается (`cqg@1.88`).

        Признак дешевизны, на котором стоит вся проверка областей, — «вписан в
        pre-commit, значит дёшев по построению: §8.6 держит бюджет коммита в 5
        секунд». Для `stages: [manual]` он НЕВЕРЕН: такой хук на коммите не
        запускается, и бюджет к нему не относится. Замер восьмого развёртывания:
        доктор гонял так вписанный `deps-audit`, а тот ходит в СЕТЬ — 24.5 с
        против обещанных «числа за две секунды». То есть инструмент врал о себе
        ровно тем же способом, который ищет.

        Своих стадий нет — те, что даст pre-commit: `default_stages:` файла, без него все
        (`cqg@3.10`). Прежде пустота читалась «на коммите» и при `default_stages: [manual]`.
        """
        own = stage_list(body)
        if own:
            return own
        default = stage_list(text, "default_stages")
        return list(STAGES) if default is None else default

    def _hook_files_re(self, text: str, name: str):
        """`files:` хука — область, ОБЪЯВЛЕННАЯ проектом, и доктор её уважает.

        Знаменатель без этого несправедлив: хук `eslint-warning-ratchet` объявлен
        как `^src/.*\\.(ts|tsx|astro)$`, и мерить его по всему дереву значило бы
        считать слепотой конфиги в корне. Расхождение доктор ищет между
        ЗАЯВЛЕНИЕМ и деревом, а не между двумя заявлениями проекта.

        Отсюда и предел, который надо знать: если и `files:`, и маска гейта
        одинаково узки, доктор молчит — сузили осознанно, и это решение проекта
        (§6). Ловится случай, которым класс и открылся: маска уже дерева, а
        объявления, что так задумано, нет.
        """
        for chunk in re.split(r"^\s*-\s+id:", text, flags=re.M)[1:]:
            if name not in chunk:
                continue
            m = re.search(r"^\s*files:\s*(\S.*?)\s*$", chunk, re.M)
            if not m:
                return None
            try:
                return re.compile(m.group(1).strip("'\""))
            except re.error:      # битую регулярку чинит pre-commit, не доктор
                return None
        return None

    #: Набор тестов в команде хука. Бюджет pre-push — 10 с (§8.6), сьют идёт
    #: минуты, и его место — CI. Признак ТЕКСТОВЫЙ, поэтому вердикт по нему —
    #: WEAK, вопрос к проекту, а не приговор (`cqg@2.47`).
    SUITE_RE = re.compile(r"\b(pytest|vitest|jest)\b|\b(npm|pnpm|yarn)\s+(run\s+)?test\b"
                          r"|\b(go|cargo)\s+test\b")

    def _push_hooks(self, text: str) -> list[tuple[str, str, bool]]:
        """(id, тело хука без комментариев, названы ли свои стадии) — всё, что git зовёт на push.

        Хук без своих стадий идёт на КАЖДОЙ установленной стадии, если `default_stages:` не
        сузил их без `pre-push`, — то есть повторяет на push работу коммита.
        """
        return [(hid, body, bool(stage_list(body))) for hid, body in self._hooks(text)
                if "pre-push" in self._hook_stages(text, body)]

    def _suite_in(self, body: str) -> str | None:
        """Какой набор тестов зовёт хук — сам или локальным скриптом из `entry:`."""
        texts = [body]
        for ref in re.findall(r"(?<![\w/.-])(scripts/[\w./-]+)", body):
            path = self.root / ref
            if path.is_file():
                texts.append("\n".join(ln for ln in path.read_text("utf-8", errors="ignore")
                                       .splitlines() if not ln.lstrip().startswith("#")))
        hit = next((m for t in texts if (m := self.SUITE_RE.search(t))), None)
        return hit.group(0) if hit else None


