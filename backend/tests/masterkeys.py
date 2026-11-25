"""Временные файлы мастер-ключа для тестов: строки случайные, живого ключа нет (b1:457)."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from ai_geo_audit import config
from ai_geo_audit.config import master_key


@dataclass(frozen=True)
class MasterFile:
    """Файл мастер-ключа теста и его строки — как записаны."""

    path: Path
    lines: list[str]


def line() -> str:
    """Строка файла: 32 случайных байта в hex, строчными."""
    return secrets.token_hex(32)


def write(path: Path, lines: list[str], *, newline: str = "\n") -> Path:
    """Файл из строк, у каждой перевод строки — и у последней."""
    path.write_bytes("".join(row + newline for row in lines).encode("ascii"))
    return path


def marker(length: int = 30) -> str:
    """Выдуманное значение ключа заданной длины, которое тест ищет во всём выводе."""
    return f"metka-{secrets.token_hex(length)}"[:length]


def use(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    """Процесс смотрит в этот файл и читает его заново — как после перезапуска."""
    monkeypatch.setattr(config, "keys", config.KeySettings(master_key_file=str(path)))
    master_key.read.cache_clear()


#: Негодные файлы A220 и соседние случаи (L53): id латиницей (L59) → причина и содержимое из
#: годной строки `row` и другой годной строки `other`. Один список на разбор (A220) и на проверку
#: старта (A221).
BAD_FILES: dict[str, tuple[str, Callable[[str, str], bytes]]] = {
    "empty": ("файл пуст", lambda row, other: b""),
    "only-newlines": ("файл пуст", lambda row, other: b"\n\n"),
    "only-crlf": ("файл пуст", lambda row, other: b"\r\n\r\n"),
    "63-signs": ("строка 1 — не 64 знака hex", lambda row, other: f"{row[:63]}\n".encode()),
    "65-signs": ("строка 1 — не 64 знака hex", lambda row, other: f"{row}a\n".encode()),
    "non-hex": ("строка 1 — знак не из hex", lambda row, other: f"g{row[1:]}\n".encode()),
    "64-non-hex": ("строка 1 — знак не из hex", lambda row, other: b"g" * 64 + b"\n"),
    "trailing-space": ("строка 1 — знак не из hex", lambda row, other: f"{row} \n".encode()),
    "trailing-tab": ("строка 1 — знак не из hex", lambda row, other: f"{row}\t\n".encode()),
    "spaces-line": ("строка 1 — знак не из hex", lambda row, other: f"   \n{row}\n".encode()),
    "bom": ("строка 1 — знак не из hex", lambda row, other: b"\xef\xbb\xbf" + row.encode()),
    "nul": ("строка 1 — знак не из hex", lambda row, other: f"{row[:5]}\0{row[6:]}".encode()),
    "not-utf8": ("строка 1 — знак не из hex", lambda row, other: b"\xff\xfe" + row[2:].encode()),
    "lone-cr": ("строка 1 — знак не из hex", lambda row, other: f"{row[:32]}\r{row[33:]}".encode()),
    "blank-then-bad": ("строка 2 — не 64 знака hex", lambda row, other: f"\n{row[:63]}".encode()),
    "good-then-bad": (
        "строка 2 — знак не из hex",
        lambda row, other: f"{other}\n{row[:63]}g\n".encode(),
    ),
    "duplicate": ("строки 1 и 2 одинаковы", lambda row, other: f"{row}\n{row}\n".encode()),
    "duplicate-case": (
        "строки 1 и 2 одинаковы",
        lambda row, other: f"{row}\n{row.upper()}\n".encode(),
    ),
    "duplicate-later": (
        "строки 1 и 3 одинаковы",
        lambda row, other: f"{row}\n{other}\n{row}\n".encode(),
    ),
}


def bad_file(case: str, row: str) -> tuple[str, bytes]:
    """Причина и содержимое негодного файла `case`, собранного из строки `row`."""
    reason, build = BAD_FILES[case]
    return reason, build(row, line())
