"""Контракт CI фронта (В1б: A381, A382, A386, A390, A393): хуки TS, объявления n/a, джоба фронта.

Правила `quality.yml` судит `quality_check` из `test_ci_contract.py` — там же `frontend_problems`;
здесь — проверки и копии, что пришли с В1б.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from test_ci_contract import (
    HOOKS,
    QUALITY,
    ROOT,
    caught_by,
    frontend_problems,
    hook,
    load,
    quality_check,
)

#: Хуки TS по шаблону канона (CQG:15633–15665): id — команда, которая обязана быть в `entry`.
TS_HOOKS = {
    "eslint": "npx --no-install eslint",
    "eslint-warning-ratchet": "scripts/lint/check_eslint_warnings.sh",
    "prettier": "npx --no-install prettier --check",
}
#: Гейты, которые судят TS-половину каталогом: правка файла фронта обязана их запускать.
TS_HALF_HOOKS = ("complexity-gate", "jscpd-dry-gate", "layers-gate")
FRONT_FILE = "frontend/src/theme/theme.ts"
NOT_APPLICABLE = ROOT / "scripts" / "lint" / "not-applicable.json"


def ts_hooks_problems(cfg: dict[Any, Any]) -> list[str]:
    """Хуки TS — на коммите, область `frontend/src`; гейты TS-половин видят правку фронта."""
    problems = []
    for hook_id, command in TS_HOOKS.items():
        found = hook(cfg, hook_id)
        checks = [
            (bool(found), f"{hook_id}: хука нет"),
            (found.get("files") == "^frontend/src/", f"{hook_id}: область не frontend/src"),
            (command in str(found.get("entry", "")), f"{hook_id}: в entry нет «{command}»"),
            (found.get("stages", ["pre-commit"]) == ["pre-commit"], f"{hook_id}: не на коммите"),
        ]
        problems += [message for ok, message in checks if not ok]
    blind = [h for h in TS_HALF_HOOKS if not caught_by(hook(cfg, h), FRONT_FILE)]
    return problems + [f"{h}: правка фронта мимо хука" for h in blind]


def test_a390_ts_hooks_follow_the_canon_template() -> None:
    """A390: eslint, ратчет предупреждений и `prettier --check` — на стадии коммита по
    `frontend/src`; гейты сложности, дублей и слоёв видят правку фронта."""
    assert ts_hooks_problems(load(HOOKS)) == []


TS_HOOK_COPIES = [
    ("prettier-removed", "prettier", "", "", "prettier: хука нет"),
    ("eslint-on-backend", "eslint", "files", "^backend/", "eslint: область не frontend/src"),
    ("ratchet-on-push", "eslint-warning-ratchet", "stages", "pre-push", "ratchet: не на коммите"),
    (
        "prettier-writes",
        "prettier",
        "entry",
        "npx --no-install prettier --write",
        "prettier: в entry",
    ),
    ("layers-without-front", "layers-gate", "files", "^backend/src/", "layers-gate: правка фронта"),
]


@pytest.mark.parametrize(
    ("case", "hook_id", "key", "value", "named"), TS_HOOK_COPIES, ids=[c[0] for c in TS_HOOK_COPIES]
)
def test_a390_ts_hook_copies_are_red_and_named(
    case: str, hook_id: str, key: str, value: str, named: str
) -> None:
    """A390, копии: хука prettier нет; eslint по backend/; ратчет на push; prettier пишет вместо
    `--check`; хук слоёв без frontend/src — красный, названо имя хука."""
    cfg = load(HOOKS)
    if not key:
        for repo in cfg["repos"]:
            repo["hooks"] = [h for h in repo["hooks"] if h["id"] != hook_id]
    else:
        hook(cfg, hook_id)[key] = [value] if key == "stages" else value
    assert named in "\n".join(ts_hooks_problems(cfg)), case


def b1b_declarations(declared: dict[str, str]) -> list[str]:
    """Ключи объявлений n/a, которые ждут поставку В1б."""
    return sorted(key for key, reason in declared.items() if "поставка В1б" in reason)


def test_a393_no_declaration_waits_for_b1b() -> None:
    """A393: записей «поставка В1б» в `not-applicable.json` нет — TS-половины, jscpd и ратчет
    eslint подключены, мета-гейт их судит."""
    assert b1b_declarations(json.loads(NOT_APPLICABLE.read_text(encoding="utf-8"))) == []


def test_a393_copy_with_b1b_declaration_is_red_and_named() -> None:
    """A393, копия: объявление TS-половины мёртвого кода «поставка В1б» вернулось — назван ключ."""
    declared = json.loads(NOT_APPLICABLE.read_text(encoding="utf-8"))
    declared["check_dead_code.py:ts"] = "поставка В1б: knip — после В1а"
    assert b1b_declarations(declared) == ["check_dead_code.py:ts"]


def test_a381_a382_a386_frontend_job_keeps_the_contract() -> None:
    """A381, A382, A386: `gates` ставит фронт до pre-commit; джоба `frontend` — Node из
    `frontend/.nvmrc`, `npm ci` и `npm run check` целиком, порчи TS только на запросе, отдельно от
    Python, бюджет 300 с, порог 60 %."""
    assert frontend_problems(load(QUALITY)) == []


FRONT_COPIES = [
    ("A381-chain-swallows-red", r"run: npm run check", "run: npm run check || true", "глотает"),
    (
        "A381-chain-continue-on-error",
        r"(- name: Цепочка фронта \(npm run check\)\n)",
        "\\g<1>        continue-on-error: true\n",
        "глотает красное",
    ),
    ("A382-node-22", r"node-version-file: frontend/\.nvmrc", 'node-version: "22"', "не из .nvmrc"),
    (
        "A382-setup-node-by-tag",
        r"actions/setup-node@[0-9a-f]{40}",
        "actions/setup-node@v7",
        "не закреплено по коммиту: jobs/gates/steps/5/uses = actions/setup-node@v7",
    ),
    (
        "A382-frontend-writes",
        r"\n  frontend:\n",
        "\n  frontend:\n    permissions:\n      contents: write\n",
        "права токена: у джобы frontend",
    ),
    (
        "A382-npm-install",
        r"run: npm ci",
        "run: npm install",
        "jobs/gates/steps/6/run — `npm install`",
    ),
    (
        "A382-front-after-hooks",
        r"\n      - name: Фронт из замка\n        working-directory: frontend\n"
        r"        run: npm ci\n",
        "\n",
        "gates: фронт не стоит до pre-commit",
    ),
    (
        "A386-mutation-on-push",
        r"\n        if: github\.event_name == 'pull_request'\n"
        r"(        env:\n          LINT_PY_SRC: нет)",
        "\n\\g<1>",
        "порчи TS не только на запросе",
    ),
    (
        "A386-judges-python",
        r"LINT_PY_SRC: нет-такого-каталога",
        'LINT_PY_SRC: "backend/src"',
        "судят Python",
    ),
    ("A386-budget", r'BUDGET_SEC: "300"', 'BUDGET_SEC: "1200"', "бюджет порч TS не 300 с"),
    (
        "A386-threshold",
        r'BUDGET_SEC: "300"\n          MIN_KILLED: "60"',
        'BUDGET_SEC: "300"\n          MIN_KILLED: "50"',
        "порог порч TS не 60 %",
    ),
]


@pytest.mark.parametrize(
    ("case", "old", "new", "named"), FRONT_COPIES, ids=[c[0] for c in FRONT_COPIES]
)
def test_a381_a382_a386_frontend_copies_are_red_and_named(
    case: str, old: str, new: str, named: str
) -> None:
    """A381, A382, A386, копии: цепочка с `|| true` и с `continue-on-error`; Node «22»; действие по
    тегу; права
    записи; `npm install`; фронт после pre-commit; порчи TS на push, по Python, с бюджетом 1200 с и
    порогом 50 % — красный, названо правило."""
    text = QUALITY.read_text(encoding="utf-8")
    spoiled, count = re.subn(old, new, text, count=1)
    assert count == 1, f"{case}: в quality.yml нет места порчи {old!r}"
    assert named in "\n".join(quality_check(spoiled)), case


def guard(half: str) -> str:
    """Предохранитель половины в тексте шага аудита — от `if` до `fi`, с отступом шага."""
    return (
        rf"\n {{10}}if printf '%s' \"\$out\" \| grep -q \"{half}-зависимости не проверены\"; then\n"
        r"[^\n]*\n {12}exit 1\n {10}fi"
    )


#: Находка 3 второй проверяющей 2026-11-26: при непроверенной половине гейт аудита печатает
#: «проверено НЕ ВСЁ» и выходит нулём — красным шаг делают только предохранители и `exit "$rc"`.
AUDIT_COPIES = [
    ("J1-js-guard-dropped", guard("js"), "", "нет предохранителя «js-зависимости не проверены»"),
    (
        "J2-python-guard-dropped",
        guard("python"),
        "",
        "нет предохранителя «python-зависимости не проверены»",
    ),
    ("J4-exit-zero", r'exit "\$rc"', "exit 0", 'итог шага — не `exit "$rc"`'),
    (
        "J5-bare-gate",
        r"run: \|\n {10}set \+e\n {10}out=\$\(bash scripts/lint/check_deps_audit\.sh 2>&1\)\n"
        r"(?: {10}.*\n)+? {10}exit \"\$rc\"\n",
        "run: bash scripts/lint/check_deps_audit.sh\n",
        "нет предохранителя «python-зависимости не проверены»",
    ),
]


@pytest.mark.parametrize(
    ("case", "old", "new", "named"), AUDIT_COPIES, ids=[c[0] for c in AUDIT_COPIES]
)
def test_a103_a389_audit_step_copies_are_red_and_named(
    case: str, old: str, new: str, named: str
) -> None:
    """A102, A103, A389, копии находки 3: снят предохранитель JS или Python, итог шага — `exit 0`,
    шаг — голый вызов гейта — красный, названо правило: непроверенная половина и рост над снимком
    иначе прошли бы зелёным."""
    text = QUALITY.read_text(encoding="utf-8")
    spoiled, count = re.subn(old, lambda _: new, text, count=1)
    assert count == 1, f"{case}: в quality.yml нет места порчи {old!r}"
    assert named in "\n".join(quality_check(spoiled)), case
