"""Файл мастер-ключа `KEYS_MASTER_KEY_FILE` (A220): строки по 64 знака hex — 32 случайных байта.
Первая строка — текущая, ею шифруются новые записи; остальные — прежние, только для расшифровки.

Заглавные hex и переводы строк CRLF принимаются: так файл возвращается из менеджера паролей
владельца. Пустые строки не в счёт, но номер строки — физический, как в редакторе. От строки
срезается только `\\r` в конце; пробел, табуляция, BOM и любой другой знак — «знак не из hex».
Одна строка в разном регистре — один ключ: повтор судится по байтам.

Разбор — в слое `config`, а не в `settings`: его зовёт и проверка старта (A221), а контракт слоёв
не пускает `config` к верхним слоям. Выводит ключи и отдаёт строки в вычистку журнала
`settings.crypto`. Файл читается байтами и целиком не раскодируется; отказ поднимается вне блока
`except` — в тексте и в цепочке исключения нет содержимого файла (A237). Файл читается один раз
за процесс.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from pathlib import Path

ENV_NAME = "KEYS_MASTER_KEY_FILE"
#: Длина строки — 32 случайных байта в hex: `openssl rand -hex 32`.
LINE_CHARS = 64
_HEX = frozenset(b"0123456789abcdefABCDEF")


class MasterKeyError(ValueError):
    """Файл мастер-ключа негоден: «KEYS_MASTER_KEY_FILE: причина» — имя переменной, номер строки
    и причина словами, без содержимого."""

    @property
    def reason(self) -> str:
        """Причина без имени переменной — так её пишет проверка старта рядом с именем."""
        return str(self).removeprefix(f"{ENV_NAME}: ")


@dataclass(frozen=True, slots=True)
class MasterKey:
    """Строка файла: её номер, текст как записан и 32 байта. В `repr` — только номер."""

    line: int
    text: str = field(repr=False)
    raw: bytes = field(repr=False)


def parse(data: bytes) -> tuple[MasterKey, ...]:
    """Ключи файла по порядку строк; негодный файл — `MasterKeyError`."""
    keys: list[MasterKey] = []
    seen: dict[bytes, int] = {}
    for number, chunk in enumerate(data.split(b"\n"), 1):
        line = chunk.removesuffix(b"\r")
        if not line:
            continue
        if not _HEX.issuperset(line):
            raise MasterKeyError(f"{ENV_NAME}: строка {number} — знак не из hex")
        if len(line) != LINE_CHARS:
            raise MasterKeyError(f"{ENV_NAME}: строка {number} — не 64 знака hex")
        text = line.decode("ascii")
        raw = bytes.fromhex(text)
        if raw in seen:
            raise MasterKeyError(f"{ENV_NAME}: строки {seen[raw]} и {number} одинаковы")
        seen[raw] = number
        keys.append(MasterKey(number, text, raw))
    if not keys:
        raise MasterKeyError(f"{ENV_NAME}: файл пуст")
    return tuple(keys)


@functools.cache
def read(path: str) -> tuple[MasterKey, ...]:
    """Ключи файла по пути — один раз за процесс; файла нет или он не читается — отказ словами."""
    if not path:
        raise MasterKeyError(f"{ENV_NAME}: не задано")
    data = _content(Path(path))
    if data is None:
        raise MasterKeyError(f"{ENV_NAME}: файл не читается: {path}")
    return parse(data)


def _content(path: Path) -> bytes | None:
    """Байты файла; ошибка чтения — None: её текст и цепочка в отказ не уходят."""
    try:
        return path.read_bytes()
    except OSError:
        return None
