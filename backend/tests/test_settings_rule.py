"""Правило «ключ — только резолвером» (A229): обход исходников пакета `ai_geo_audit` по дереву.

Пакет — импортированный, по `ai_geo_audit.__file__` (L95): под порчами он в `backend/mutants/src`.
Вне `settings/` никто не импортирует строку ключа, модели и шифр и не пишет запрос к
`service_keys`; резолвер ключа нигде не зовётся при импорте модуля (A227). Резолвер узнаётся по
импортам модуля, а не по имени: `Path(__file__).resolve()` — не ключ. Безымянный импорт моделей —
поимённое исключение для одного `ai_geo_audit/schema.py`.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest

import ai_geo_audit

KEYS, MODELS = "ai_geo_audit.settings.keys", "ai_geo_audit.settings.models"
SCHEMA = ("ai_geo_audit", "schema.py")
#: Строка ключа и шифр: звенья `models` и `crypto` пакета настроек, имена `ServiceKey` и `unseal`.
NAMES = (".ServiceKey", ".unseal")
OUTSIDE = "строка ключа, модели или шифр вне settings/"
QUERY = "запрос к service_keys вне settings/"
AT_IMPORT = "резолвер ключа при импорте модуля — ключ берётся в момент вызова"


def _bound(node: ast.AST, parts: tuple[str, ...]) -> Iterator[tuple[str, str]]:
    """Пары «полное имя — имя в модуле» у импорта; относительный — от пакета модуля."""
    if isinstance(node, ast.Import):
        yield from ((alias.name, alias.asname or alias.name) for alias in node.names)
    elif isinstance(node, ast.ImportFrom):
        home = parts[: len(parts) - node.level] if node.level else ()
        module = ".".join([*home, *filter(None, [node.module])])
        yield from ((f"{module}.{alias.name}", alias.asname or alias.name) for alias in node.names)


def _full(node: ast.expr, bound: dict[str, str]) -> str:
    """Полное имя цепочки `a.b.c`: первое звено — по импортам модуля (находка 15)."""
    head, _, rest = ast.unparse(node).partition(".")
    return ".".join(filter(None, [bound.get(head, head), rest]))


def _private(full: str) -> bool:
    inner = full.split(".")[2:] if full.startswith("ai_geo_audit.settings.") else []
    return bool({"models", "crypto"} & set(inner)) or full.endswith(NAMES)


def _at_import(node: ast.AST) -> Iterator[ast.AST]:
    """Узлы, что исполняются при импорте: всё, кроме тел функций и лямбд; их умолчания и
    декораторы исполняются при определении."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            made = [*child.args.defaults, *child.args.kw_defaults]
            for part in filter(None, [*made, *getattr(child, "decorator_list", [])]):
                yield part
                yield from _at_import(part)
        else:
            yield child
            yield from _at_import(child)


def _broken(tree: ast.Module, parts: tuple[str, ...]) -> Iterator[tuple[int, str]]:
    bound = {name: full for node in ast.walk(tree) for full, name in _bound(node, parts)}
    for node in _at_import(tree):
        if isinstance(node, ast.Call) and _full(node.func, bound) == f"{KEYS}.resolve":
            yield node.lineno, AT_IMPORT
    if parts[1:2] == ("settings",):
        return
    for node in ast.walk(tree):
        nameless = parts == SCHEMA and ast.unparse(node) == f"import {MODELS}"
        if isinstance(node, ast.Import | ast.ImportFrom) and not nameless:
            if any(_private(full) for full, _ in _bound(node, parts)):
                yield node.lineno, OUTSIDE
        elif isinstance(node, ast.Attribute) and _private(_full(node, bound)):
            yield node.lineno, OUTSIDE
        elif isinstance(node, ast.Constant) and "service_keys" in str(node.value).lower():
            yield node.lineno, QUERY


def violations(package: Path) -> tuple[int, list[str]]:
    """Модулей просмотрено и нарушения «путь:строка: что». Ноль модулей — отказ, а не «нарушений
    нет»."""
    paths = sorted(package.rglob("*.py"))
    if not paths:
        raise AssertionError(f"модулей не найдено: {package} — правило ничего не судило")
    found = []
    for path in paths:
        parts = path.relative_to(package.parent).parts
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        broken = dict.fromkeys(_broken(tree, parts))  # вложенная цепочка — одна запись на строку
        found += [f"{'/'.join(parts)}:{line}: {what}" for line, what in broken]
    return len(paths), found


def test_a229_the_package_keeps_the_rule() -> None:
    """A229: на настоящем пакете — 0 нарушений, модулей просмотрено больше 0."""
    seen, found = violations(Path(ai_geo_audit.__file__).parent)
    assert (found, seen > 0) == ([], True), f"модулей просмотрено {seen}"


def test_a229_no_modules_is_a_refusal(tmp_path: Path) -> None:
    """A229: ноль модулей — отказ, а не «нарушений нет»."""
    with pytest.raises(AssertionError) as refused:
        violations(tmp_path)
    assert refused.value.args == (f"модулей не найдено: {tmp_path} — правило ничего не судило",)


def test_a229_violations_of_every_module_are_kept(tmp_path: Path) -> None:
    """A229: нарушения копятся по всем модулям пакета, а не по последнему."""
    package = tmp_path / "ai_geo_audit"
    package.mkdir()
    (package / "a.py").write_text(f"from {MODELS} import ServiceKey\n", encoding="utf-8")
    (package / "b.py").write_text("", encoding="utf-8")
    assert violations(package) == (2, [f"ai_geo_audit/a.py:1: {OUTSIDE}"])


IMPORT = f"from {KEYS} import resolve\n\n"
#: Пробы — по одной на нарушение: модуль, текст, строка и что сказано; `None` — сосед без
#: нарушений. Безымянный импорт моделей в `storage/schema.py` — красное: исключение — по пути.
#: Обходы проверки по имени (пункты второй проверяющей): относительный импорт, таблица заглавными,
#: резолвер под другим именем, вызов под `if` и в списковом выражении. `relative-module` держит
#: само приведение уровня. Находки 15–16 — атрибут и пакет, умолчание по имени, вложенный вызов.
CASES: dict[str, tuple[str, str, tuple[int, str] | None]] = {
    "key-row": ("api/p.py", f"from {MODELS} import ServiceKey\n", (1, OUTSIDE)),
    "cipher": ("api/p.py", "from ai_geo_audit.settings import crypto\n", (1, OUTSIDE)),
    "unseal": ("api/p.py", "from ai_geo_audit.settings.crypto import unseal\n", (1, OUTSIDE)),
    "relative": ("api/p.py", "from ..settings.crypto import unseal\n", (1, OUTSIDE)),
    "relative-module": ("api/p.py", "from ..settings import crypto\n", (1, OUTSIDE)),
    "nameless-elsewhere": ("storage/schema.py", f"import {MODELS}\n", (1, OUTSIDE)),
    "query": ("api/p.py", 'ROWS = "SELECT ciphertext FROM service_keys"\n', (1, QUERY)),
    "query-upper": ("api/p.py", 'ROWS = "SELECT * FROM SERVICE_KEYS"\n', (1, QUERY)),
    "module-level": ("api/p.py", f"{IMPORT}K = resolve(0, 'x')\n", (3, AT_IMPORT)),
    "renamed": (
        "api/p.py",
        f"from {KEYS} import resolve as get\nK = get(0, 'x')\n",
        (2, AT_IMPORT),
    ),
    "full-path": ("api/p.py", f"import {KEYS}\nK = {KEYS}.resolve(0, 'x')\n", (2, AT_IMPORT)),
    "under-if": ("api/p.py", f"{IMPORT}if True:\n    K = resolve(0, 'x')\n", (4, AT_IMPORT)),
    "comprehension": ("api/p.py", f"{IMPORT}K = [resolve(0, n) for n in 'ab']\n", (3, AT_IMPORT)),
    "decorator": ("api/p.py", f"{IMPORT}@print(resolve())\ndef f(): ...\n", (3, AT_IMPORT)),
    "kw-default": ("api/p.py", f"{IMPORT}def f(*, k=list(resolve())): ...\n", (3, AT_IMPORT)),
    "keys-row": ("api/p.py", f"from {KEYS} import ServiceKey\n", (1, OUTSIDE)),
    "keys-unseal": ("api/p.py", f"from {KEYS} import unseal\n", (1, OUTSIDE)),
    "attribute": ("api/p.py", f"import {KEYS} as keys\nROW = keys.ServiceKey\n", (2, OUTSIDE)),
    "chain": ("api/p.py", f"import {KEYS} as k\nk.crypto.unseal()\n", (2, OUTSIDE)),
    "parent": ("x.py", "import ai_geo_audit.settings as s\ns.keys.resolve()\n", (2, AT_IMPORT)),
    "default": (
        "settings/p.py",
        f"{IMPORT}def read(k=resolve(0, 'x')):\n    pass\n",
        (3, AT_IMPORT),
    ),
    "schema": ("schema.py", f"import {MODELS}  # noqa: F401\n", None),
    "path-resolve": (
        "api/p.py",
        "from pathlib import Path\nROOT = Path(__file__).resolve()\n",
        None,
    ),
    "in-a-function": (
        "api/p.py",
        f"{IMPORT}async def read(s):\n    return await resolve(s, 0)\n",
        None,
    ),
    "inside-settings": ("settings/p.py", f"from {MODELS} import ServiceKey\n", None),
}


@pytest.mark.parametrize("case", list(CASES))
def test_a229_each_probe_is_red_with_its_path_and_line(tmp_path: Path, case: str) -> None:
    """A229: каждая проба — красное с путём и номером строки; соседи проб — без нарушений."""
    relative, source, broken = CASES[case]
    (tmp_path / "ai_geo_audit" / relative).parent.mkdir(parents=True)
    (tmp_path / "ai_geo_audit" / relative).write_text(source, encoding="utf-8")
    found = [f"ai_geo_audit/{relative}:{broken[0]}: {broken[1]}"] if broken else []
    assert violations(tmp_path / "ai_geo_audit") == (1, found)
