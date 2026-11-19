"""Отказ процесса при негодной настройке — один текст для всех входов процессов.

Конфиг собирается при импорте, поэтому негодная настройка роняет `import` раньше любого `try`
вокруг работы процесса: вход ловит `ValidationError` вокруг импорта пакета и строит текст здесь.
Модуль конфиг не импортирует — иначе упал бы вместе с ним. Причина — из
`errors(include_input=False)`: введённого значения pydantic в тексте нет; значение бывает только
в тексте своей проверки («…а не 0», «`*` нельзя»).
"""

from __future__ import annotations

from pydantic import ValidationError


def invalid_settings(process: str, invalid: ValidationError) -> str:
    """Текст отказа: процесс и по строке на каждую негодную настройку — имя и причина."""
    reasons = "\n".join(
        f"  {'.'.join(map(str, error['loc']))} — {error['msg'].removeprefix('Value error, ')}"
        for error in invalid.errors(include_url=False, include_input=False)
    )
    return f"Процесс «{process}» не стартует, негодные настройки:\n{reasons}"
