#!/usr/bin/env python3
"""Что УПАЛО в красных прогонах CI — и что это говорит о гейте (`cqg@3.08`).

Часть `check_gate_value.sh`: вход добывает прогоны и логи у `gh`, здесь суждение.

До `3.08` срабатыванием считалось ИМЯ файла гейта в логе красного прогона. А `gh run view
--log-failed` отдаёт лог двух форм: упавший шаг под его именем — либо, когда строки не
привязаны к шагу (`UNKNOWN STEP`), упавший job ЦЕЛИКОМ, где гейт назван всегда: командой
шага, который прошёл, таблицей доктора. «Сработавшим» выходил каждый гейт.

Срабатывание — гейт УПАЛ:
  шаг CI — упал шаг (`##[error]Process completed with exit code`), чья команда его зовёт;
  хук    — итог `Failed` в сводке pre-commit и `- hook id:` под ним; `Passed` и `Skipped`
           не в счёт, хотя `--verbose` пишет id и под ними.
Единица счёта — правило, где его отличает id хука или `--rule` шага, иначе файл (бюджет
CQG §3 считает правила). Гейт на коммите или push ловит ДО CI, и это нигде не записано:
ноль в CI про него не говорит ничего — ему пишется «по CI не судится», а не «удалить».
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from doctor_hooks import HookReaders

#: Строка лога: время раннера впереди (у первой строки файла — ещё и BOM), цвет внутри.
STAMP = re.compile("^" + chr(0xFEFF) + r"?\d{4}-\d\d-\d\dT[\d:.]+Z ?")
ANSI = re.compile(r"\x1b\[[0-9;]*m")
STEP = "##[group]Run "
FELL = "##[error]Process completed with exit code"
#: Итог хука в сводке pre-commit: точки, необязательная пометка в скобках, слово итога.
RESULT = re.compile(r"\.{3,}(?:\(.*\))?(Passed|Failed|Skipped)\s*$")
HOOK_ID = "- hook id: "
#: Что уронило красный прогон помимо гейтов — одной строкой итога, без разбора.
NO_GATE, ALIEN_HOOK, SEVERAL, NO_STEP = ("шаг без гейта", "хук вне инвентаря",
                                         "несколько гейтов в одном шаге", "упавший шаг не найден")
COLUMNS = ("гейт", "единица", "узнаётся по", "ронял", "вердикт (Delivery §9.1a п.2)")


@dataclass
class Unit:
    """Единица счёта: файл гейта либо его правило."""
    file: str
    rule: str
    hooks: list = field(default_factory=list)   # «id (стадия)» хуков, которые её зовут
    local: str = ""                             # стадия хука, который ловит мимо CI
    in_ci: bool = False                         # её зовёт шаг просмотренного workflow
    fell: int = 0                               # красных прогонов, где она упала

    def row(self, said: str) -> tuple:
        seen = ["хук " + h for h in self.hooks] + (["шаг CI"] if self.in_ci else [])
        name = f"{self.file} --rule {self.rule}" if self.rule else self.file
        return (name, "правило" if self.rule else "файл", ", ".join(seen) or "—",
                str(self.fell), said)


def hooks_of(config: str) -> list:
    """[(id, тело, стадии)] хуков `.pre-commit-config.yaml`; тело хука — до следующего id.

    Хуки и стадии читает правило доктора (`HookReaders._hooks`, `_hook_stages`) — одно
    чтение на всех (`cqg@3.10`): стадии ровно те, на которых хук запустит pre-commit, и
    `default_stages:` файла доходит до хука без своих.
    """
    reader = HookReaders()
    return [(hid, body, reader._hook_stages(config, body)) for hid, body in reader._hooks(config)]


def stage_of(stages: list) -> tuple:
    """(как назвать стадию; запускается ли хук сам, у разработчика, — то есть ловит мимо CI).

    Сам не запускается только `manual`. Старые имена (`commit`, `push`) правило доктора уже
    перевело; стадия, которой здесь нет слова (`commit-msg` и т. п.), — своя у разработчика:
    она не делает гейт «кандидатом».
    """
    named = set(stages)
    if named == {"manual"}:
        return "manual", False
    if "pre-commit" in named:
        return "коммит", True
    if "pre-push" in named:
        return "push", True
    return ",".join(stages), True


def workflow_of(name: str) -> str | None:
    """Текст workflow, чьи прогоны смотрим: файл с этим именем или с этим `name:`."""
    found = []
    for path in sorted(Path(".github/workflows").glob("*.y*ml")):
        text = path.read_text(encoding="utf-8", errors="replace")
        m = re.search(r"(?m)^name:\s*(.+?)\s*$", text)
        if name in (path.name, path.stem) or (m and m.group(1).strip("'\"") == name):
            found.append(text)
    return "\n".join(found) if found else None


class Wiring:
    """Единицы счёта и то, по чему их узнают в логе: хуки и шаги, которые их зовут."""

    def __init__(self, names: list, config: str, workflow: str | None) -> None:
        self.gates = {g: re.compile(rf"(?<![\w.-]){re.escape(g)}(?![\w.-])"
                                    r"(?:[\"']?\s+--rule\s+([\w-]+))?") for g in names}
        hooks = [(hid, self.calls(body), stages) for hid, body, stages in hooks_of(config)]
        steps = [self.calls(line) for line in (workflow or "").splitlines()]
        self.units = self._units(names, [*[c for _, c, _ in hooks], *steps])
        self.by_hook = self._wire(hooks, steps)

    def calls(self, text: str) -> set:
        """Вызовы гейтов в команде или хуке: {(файл, правило)}; без `--rule` правило пусто.

        Имя — словом пути, а не подстрокой; строка-комментарий ничего не зовёт. Файл,
        названный и с правилом, и без (охрана `[ -f … ]` перед вызовом), — вызов правила.
        """
        code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
        found = set()
        for gate, pattern in self.gates.items():
            rules = set(pattern.findall(code))
            found |= {(gate, r) for r in (rules - {""} or rules)}
        return found

    def _units(self, names: list, called: list) -> dict:
        """Правило — своя единица, когда его вызов стоит ОДИН в хуке или в строке workflow:
        тогда его падение отличимо от падения соседа. Иначе единица — файл."""
        alone = {next(iter(c)) for c in called if len(c) == 1}
        units = {}
        for g in names:
            for key in sorted(k for k in alone if k[0] == g and k[1]) or [(g, "")]:
                units[key] = Unit(*key)
        return units

    def targets(self, found: set) -> set:
        """Единицы, которые зовёт текст: вызов неразличимого правила — это вызов файла."""
        keys = {k if k in self.units else (k[0], "") for k in found}
        return {k for k in keys if k in self.units}

    def _wire(self, hooks: list, steps: list) -> dict:
        """{id хука: его единицы}; попутно — где каждая единица исполняется."""
        by_hook = {}
        for hid, found, stages in hooks:
            label, on_its_own = stage_of(stages)
            by_hook[hid] = self.targets(found)
            for key in by_hook[hid]:
                self.units[key].hooks.append(f"{hid} ({label})")
                if on_its_own and self.units[key].local != "коммит":
                    self.units[key].local = label
        for found in steps:
            for key in self.targets(found):
                self.units[key].in_ci = True
        return by_hook

    def called_by(self, step: dict) -> tuple:
        """(наборы единиц, которые звал упавший шаг; как назвать набор без единиц). Шаг со
        сводкой pre-commit судится по хукам с итогом `Failed`, прочий — по своей команде."""
        bad = failed_hooks(step["out"])
        if bad:
            return [self.by_hook.get(h, set()) for h in bad], ALIEN_HOOK
        return [self.targets(self.calls("\n".join(step["cmd"])))], NO_GATE

    def causes(self, log: str) -> tuple:
        """(единицы, упавшие в прогоне; что уронило его помимо гейтов). Шаг или хук, зовущий
        несколько единиц, догадкой не делится: по коду возврата не видно, которая упала."""
        fired, other = set(), set()
        fallen = [s for s in steps_of(log) if s["fell"]]
        if not fallen:
            other.add(NO_STEP)
        for step in fallen:
            called, nobody = self.called_by(step)
            for keys in called:
                if len(keys) == 1:
                    fired |= keys
                else:
                    other.add(SEVERAL if keys else nobody)
        return fired, other


def steps_of(log: str) -> list:
    """Шаги лога по порядку: команда, вывод, упал ли. Обе формы вывода `gh` — одним проходом.

    Шаг — от `##[group]Run ` до следующего такого же: так раннер открывает и `run:`, и `uses:`,
    целым ли job'ом отдан лог или одним шагом. Команда — строки заголовка до `shell:`; упал —
    в шаге есть строка раннера о коде возврата (отмена, таймаут и упавший `uses:` её не пишут).
    """
    steps = [{"cmd": [], "out": [], "head": False, "fell": False}]
    for raw in log.splitlines():
        text = ANSI.sub("", STAMP.sub("", raw.split("\t", 2)[-1]))
        if text.startswith(STEP):
            steps.append({"cmd": [], "out": [], "head": True, "fell": False})
        cur = steps[-1]
        if text.startswith(FELL):
            cur["fell"] = True
        elif cur["head"] and not text.startswith(("shell: ", "##[endgroup]")):
            cur["cmd"].append(text[len(STEP):] if text.startswith(STEP) else text)
        else:
            cur["head"] = False
            cur["out"].append(text)
    return steps


def failed_hooks(out: list) -> list:
    """id хуков с итогом `Failed`: строка итога в сводке pre-commit и `- hook id:` под ней."""
    failed, pending = [], False
    for line in out:
        m = RESULT.search(line)
        if m:
            pending = m.group(1) == "Failed"
        elif pending and line.startswith(HOOK_ID):
            failed.append(line[len(HOOK_ID):].strip())
            pending = False
    return failed


def verdict(unit: Unit, read: int, empty: int, workflow: str) -> str:
    """Что счёт говорит о гейте по Delivery §9.1a п.2 — и говорит ли вообще."""
    if unit.fell:
        return f"ронял красные прогоны: {unit.fell} из {read}"
    if unit.local:
        where = {"коммит": "на коммите", "push": "на push"}.get(unit.local, f"у разработчика ({unit.local})")
        return f"в CI не падал, но исполняется {where} — по CI не судится"
    if not unit.in_ci:
        return f'в CI не падал, шага с ним в workflow "{workflow}" нет — по этим прогонам не судится'
    if empty:
        return f"в прочитанных логах не падал; красных без лога: {empty} — кандидатом не назван"
    return "в CI не падал и исполняется только там — кандидат §9.1a: удалить либо gate_kept_reason"


def red_logs(work: str) -> tuple:
    """(прогонов всего; красных; логи красных, которые не пусты)."""
    runs = [l.split() for l in Path(work, "runs").read_text(encoding="utf-8").splitlines() if l.strip()]
    red = [Path(work, f"{r[0]}.log") for r in runs if r[1:] == ["failure"]]
    logs = [p.read_text(encoding="utf-8", errors="replace") for p in red if p.is_file()]
    return len(runs), len(red), [l for l in logs if l.strip()]


def tally(wiring: Wiring, logs: list) -> tuple:
    """Разнести падения по единицам. → (прогонов, где упал гейт; что роняло помимо гейтов)."""
    beside, by_gate = Counter(), 0
    for log in logs:
        fired, other = wiring.causes(log)
        by_gate += bool(fired)
        beside.update(other)
        for key in fired:
            wiring.units[key].fell += 1
    return by_gate, beside


def table(rows) -> str:
    """Колонки по ширине содержимого; вердикт — последним, без выравнивания."""
    rows = [COLUMNS, *rows]
    width = [max(len(r[i]) for r in rows) for i in range(4)]
    return "\n".join("  ".join([*(c.ljust(w) for c, w in zip(r, width)), r[4]]) for r in rows)


def main(work: str, workflow: str, names: list) -> int:
    total, red, logs = red_logs(work)
    empty = red - len(logs)
    config = Path(".pre-commit-config.yaml")
    text = workflow_of(workflow)
    wiring = Wiring(names, config.read_text(encoding="utf-8", errors="replace")
                    if config.is_file() else "", text)
    by_gate, beside = tally(wiring, logs)
    print(f'gate-value: workflow "{workflow}" — прогонов {total}, красных {red}: '
          f"лог прочитан у {len(logs)}, пуст у {empty}")
    if empty:
        print(f"⚠ красных прогонов без лога: {empty} (хостинг хранит логи ограниченно). Что уронило "
              "их, неизвестно — в счёт они не вошли, и кандидаты не называются.")
    if text is None:
        print(f'⚠ файла workflow "{workflow}" в .github/workflows нет — что он зовёт, не видно: '
              "«исполняется только в CI» не пишется никому.")
    print("\nСрабатывание — гейт УПАЛ в красном прогоне: упал шаг CI с его вызовом либо хук pre-commit\n"
          "с итогом Failed. Гейт, названный в логе и прошедший, не в счёт. Гейт на коммите или push\n"
          "ловит до CI, и это нигде не записано: ноль в CI — не его молчание.\n")
    print(table(u.row(verdict(u, len(logs), empty, workflow)) for u in wiring.units.values()))
    print(f"\nкрасных прогонов с логом {len(logs)}: гейт упал в {by_gate}; помимо гейтов роняли "
          "(прогонов) — " + (", ".join(f"{k}: {n}" for k, n in sorted(beside.items())) or "ничего"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3:]))
