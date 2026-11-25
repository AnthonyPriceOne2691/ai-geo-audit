"""Где тест ищет метку в отказе (A237): всё, что исключение несёт наружу."""

from __future__ import annotations


def traces(error: BaseException) -> str:
    """Текст, `repr`, аргументы, заметки и поля `.object` и `.doc` — по всей цепочке `__cause__` и
    `__context__`, без оглядки на `__suppress_context__`: `raise … from None` прячет звено
    только при печати, а держит его по-прежнему."""
    parts: list[str] = []
    seen: set[int] = set()
    stack: list[BaseException] = [error]
    while stack:
        item = stack.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        parts += [str(item), repr(item), *map(repr, item.args)]
        parts += [repr(note) for note in getattr(item, "__notes__", [])]
        parts += [repr(getattr(item, name)) for name in ("object", "doc") if hasattr(item, name)]
        stack += [link for link in (item.__cause__, item.__context__) if link is not None]
    return "\n".join(parts)


def links(error: BaseException) -> int:
    """Сколько звеньев в цепочке, кроме самого исключения: у отказа, поднятого вне `except`, — 0."""
    count = 0
    item = error.__cause__ or error.__context__
    while item is not None:
        count += 1
        item = item.__cause__ or item.__context__
    return count
