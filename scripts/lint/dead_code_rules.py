#!/usr/bin/env python3
"""Правила гейта мёртвого кода: что зовут НЕ по имени (python-половина).

Часть `check_dead_code.py` (`cqg@3.09`), разрез — по вопросу, а не по размеру: вход
гоняет инструменты, держит снимок и выносит вердикт; здесь — шесть правил, которые
снимают ложное у vulture. Он судит по имени: объявление живо, если имя где-то читают.
Фреймворк зовёт иначе — декоратором, рефлексией, по строке, — и такое объявление
vulture назвал бы мёртвым (замер 26.10 на трёх проектах: 401 сырая находка, 38 в остатке).

У каждого правила — оракул «не прячет настоящее мёртвое»: правило шире своего
принципа хуже ложного красного — оно молчит. Чего правила не знают, называет проект
строкой с причиной (`dead-code-allowed.json`), а не канон списком на всех.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from pathlib import Path
from typing import NamedTuple

from ast_web_rules import _ROUTE_DECOR


class NotJudgedError(Exception):
    """Гейт не просудил: нет инструмента, он упал, ответ не разобран, смотреть некуда."""


# ① Декоратор РЕГИСТРИРУЕТ объявление: фреймворк зовёт его сам — по маршруту и событию
# (глаголы — одно понятие с AST-гейтом, `_ROUTE_DECOR`), командой CLI, из очереди, при
# валидации, фикстурой. Обёртка (`lru_cache`, свой `retry`) — не регистрация:
# обёрнутую функцию всё равно кто-то обязан позвать.
REGISTRAR = re.compile(
    rf"\.({'|'.join(_ROUTE_DECOR)}|head|options|websocket|on_event|middleware|exception_handler"
    r"|command|callback|group|scheduled_job)$"
    r"|(^|\.)(fixture|task|job|actor|shared_task|validates|listens_for|computed_field"
    r"|\w*validator|\w*serializer)$")
# ② Имя, зарезервированное фреймворком, — там, где он его читает: `model_config` у настроек
# pydantic (у модели поле живо по ③). В обычном классе это имя — обычное, и список узок.
RESERVED = frozenset(("model_config",))
# ③ Поле класса, который читают НЕ по имени. Класс-данные — контракт сериализации:
# поле читает и пишет фреймворк (pydantic, ORM, ключи TypedDict, позиции NamedTuple);
# база ищется и через классы проекта (`Card(_Base)`), примесь к модели — тоже модель.
# Перечисление — только то, что читают ПО ЗНАЧЕНИЮ: в коде есть вызов `Имя(…)`.
# Настройки, прочие перечисления и датаклассы СУДЯТСЯ: замер 26.10 — правило «по базе»
# спрятало мёртвое в 3 полях из 15 (две настройки и член перечисления), замер 14.11 —
# в 1 поле датакласса из 4.
CONTRACT = frozenset(("BaseModel", "RootModel", "SQLModel", "DeclarativeBase",
                      "TypedDict", "NamedTuple"))
ENUMS = frozenset(("Enum", "StrEnum", "IntEnum", "Flag", "IntFlag"))
JUDGED = ENUMS | {"BaseSettings"}
CLASS_RE = re.compile(r"(?m)^[ \t]*class[ \t]+(\w+)[ \t]*\(([^)]*)\)")
CALL_RE = re.compile(r"(?<![\w.])(?<!class )([A-Z]\w*)\(")
# ④ Имя названо СТРОКОЙ-АДРЕСОМ — `модуль.имя` или `модуль:имя` целиком в кавычках, и
# модуль тот самый: так зовут очереди, консольные команды (`[project.scripts]`),
# загрузчики. Одного имени в кавычках мало: ключ словаря `"too_long"` функцию не зовёт.
ADDR_RE = re.compile(r"""["']([A-Za-z_][\w.]*)[.:]([A-Za-z_]\w*)["']""")
# ⑤ Атрибут судится только у СВОЕГО объекта (`self.x = …`). Запись в чужой —
# настройка библиотеки (`resolver.lifetime = …`), читает её она: замер — 0 мёртвых из 7.
# ⑥ Переопределение (`@override`, PEP 698) зовёт база класса по своему имени: `logging.Handler`
# зовёт `handleError` сам. Только в теле класса — функции вне класса переопределять нечего.
OVERRIDE = re.compile(r"(^|\.)override$")


class Known(NamedTuple):
    """Что правила знают о проекте — один проход по текстам источников."""

    bases: dict[str, set[str]]   # класс → его базы, по именам
    mixins: set[str]             # предки классов-данных: их поля — поля модели
    called: set[str]             # имена с заглавной, которые зовут: `Имя(…)`
    addr: dict[str, set[str]]    # имя → модули из строк-адресов


def base_name(text: str) -> str:
    """`pkg.Base[T]` → `Base`: база — по последнему имени, без параметров."""
    return re.sub(r"\[.*", "", text).strip().rsplit(".", 1)[-1]


def lineage(names: Iterable[str], bases: dict[str, set[str]]) -> set[str]:
    """Эти классы и все их предки по именам — через классы проекта."""
    seen: set[str] = set()
    todo = list(names)
    while todo:
        cur = todo.pop()
        if cur not in seen:
            seen.add(cur)
            todo.extend(bases.get(cur, ()))
    return seen


def index(root: Path, texts: list[str]) -> Known:
    bases: dict[str, set[str]] = {}
    addr: dict[str, set[str]] = {}
    called: set[str] = set()
    for rel in texts:
        text = (root / rel).read_text(encoding="utf-8", errors="replace")
        for name, raw in CLASS_RE.findall(text):
            bases.setdefault(name, set()).update(
                base_name(b) for b in raw.split(",") if "=" not in b)
        for module, name in ADDR_RE.findall(text):
            addr.setdefault(name, set()).add(module)
        called.update(CALL_RE.findall(text))
    mixins: set[str] = set()
    for own in bases.values():
        line = lineage(own, bases)
        if line & CONTRACT and not line & JUDGED:
            mixins |= line
    return Known(bases, mixins, called, addr)


def dotted(node: ast.AST) -> str:
    return ast.unparse(node.func if isinstance(node, ast.Call) else node)


def decorators(tree: ast.AST, line: int, name: str) -> list[str]:
    """Декораторы объявления: vulture называет строку ПЕРВОГО декоратора."""
    for node in ast.walk(tree):
        decs = getattr(node, "decorator_list", None)
        if decs and getattr(node, "name", "") == name and decs[0].lineno == line:
            return [dotted(d) for d in decs]
    return []


def overrides(tree: ast.AST, line: int, name: str) -> bool:
    """⑥ Метод прямо в теле класса, и среди его декораторов — `override`."""
    return any(isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and stmt.name == name
               and bool(stmt.decorator_list) and stmt.decorator_list[0].lineno == line
               and any(OVERRIDE.search(dotted(d)) for d in stmt.decorator_list)
               for node in ast.walk(tree) if isinstance(node, ast.ClassDef) for stmt in node.body)


def owner(tree: ast.AST, line: int) -> ast.ClassDef | None:
    """Класс, в ТЕЛЕ которого стоит присваивание на этой строке; None — не поле."""
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and any(
                isinstance(stmt, (ast.Assign, ast.AnnAssign))
                and stmt.lineno <= line <= (stmt.end_lineno or line) for stmt in node.body):
            return node
    return None


def read_not_by_name(cls: ast.ClassDef, name: str, known: Known) -> bool:
    """Правила ② и ③ — по СВОИМ базам класса: имя класса в проекте бывает не одно."""
    line = lineage({base_name(ast.unparse(b)) for b in cls.bases}, known.bases)
    if line & JUDGED:
        return cls.name in known.called if line & ENUMS else name in RESERVED
    return bool(line & CONTRACT) or cls.name in known.mixins


def is_own_attribute(tree: ast.AST, line: int, name: str) -> bool:
    return any(isinstance(n, ast.Attribute) and n.attr == name and n.lineno == line
               and isinstance(n.ctx, ast.Store) and isinstance(n.value, ast.Name)
               and n.value.id in ("self", "cls") for n in ast.walk(tree))


def alive(tree: ast.AST, finding: tuple, known: Known) -> bool:
    """True — объявление зовут не по имени: находка vulture ложная (правила ①–⑥)."""
    path, line, kind, name = finding
    registered = any(REGISTRAR.search(d) for d in decorators(tree, line, name))
    if registered or overrides(tree, line, name):
        return True
    module = "." + path[:-3].replace("/", ".").removesuffix(".__init__")
    if any(module.endswith("." + m) for m in known.addr.get(name, ())):
        return True
    if kind == "attribute":
        return not is_own_attribute(tree, line, name)
    cls = owner(tree, line) if kind == "variable" else None
    return cls is not None and read_not_by_name(cls, name, known)


def parsed(root: Path, path: str) -> ast.AST:
    try:
        return ast.parse((root / path).read_text(encoding="utf-8"))
    except (SyntaxError, ValueError) as exc:
        raise NotJudgedError(f"python: правила не разобрали {path} этим интерпретатором ({exc}) — "
                             "запусти гейт питоном проекта (venv)") from exc


def python_dead(root: Path, rows: list[tuple], texts: list[str],
                population: set[str]) -> dict[str, list[str]]:
    """Находки vulture → {ключ снимка: места}. Судится население; импорты (их держит
    ruff `F401`) и аргументы (уверенность 100 %: подпись диктует вызывающий) — нет."""
    known = index(root, texts)
    trees: dict[str, ast.AST] = {}
    found: dict[str, list[str]] = {}
    for path, line, kind, name, confidence in rows:
        if path not in population or kind == "import" or (kind, confidence) == ("variable", 100):
            continue
        if path not in trees:
            trees[path] = parsed(root, path)
        if kind != "unreachable" and alive(trees[path], (path, line, kind, name), known):
            continue
        what = name if kind != "unreachable" else f"(недостижимо после {name})"
        found.setdefault(f"{path}::{what}", []).append(f"{path}:{line}")
    return found
