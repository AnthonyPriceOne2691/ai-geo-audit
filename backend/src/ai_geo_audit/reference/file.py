"""Файл справочника, формат 1 (A305–A307): чтение с пределом, разбор, схема и причины отказа.

JSON в UTF-8 без BOM: `format`, `source_date`, `rows`. Числа — точные десятичные из текста JSON:
`NaN` и `Infinity` — отказ разбора, булево на месте числа — своя причина до схемы, равенство —
значением (L131; decisions — проба 2). Код — `fullmatch` с цифрами ASCII (L132). Проверка собирает
все причины за прогон; причина — «строка N, код K: что не так», без значений и текстов файла
(A307): места и виды ошибок схемы — из `errors(include_input=False)`, слова — свои. Пределы порога —
таблица единиц: ревью видит её здесь, при записи они ложатся в редакцию.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import IO, Annotated, Any, Final

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from ai_geo_audit.reference.models import Coverage, Importance, Method, Scope, Unit

MAX_BYTES: Final = 1_048_576
MAX_ROWS: Final = 200
FORMAT: Final = 1
TEXT_MAX: Final = 4000
NAME_MAX: Final = 200
#: Пределы порога по единице — ответ проверяющего 2026-11-20T11:27Z.
UNITS: Final[dict[Unit, tuple[Decimal, Decimal]]] = {
    Unit.SECONDS: (Decimal("0.1"), Decimal("60")),
    Unit.MILLISECONDS: (Decimal("1"), Decimal("60000")),
    Unit.MEGABYTES: (Decimal("0.1"), Decimal("100")),
    Unit.PERCENT: (Decimal("0.1"), Decimal("100")),
    Unit.CHARS: (Decimal("1"), Decimal("100000")),
    Unit.CLICKS: (Decimal("1"), Decimal("50")),
    Unit.COUNT: (Decimal("0"), Decimal("100000")),
    Unit.NUMBER: (Decimal("0"), Decimal("10")),
}
_CODE: Final = re.compile(r"[0-9]{1,2}(\.[0-9]{1,2})?")
_NAME: Final = re.compile(r"[a-z][a-z0-9_]{0,39}")
_DATE: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_TOP: Final = ("format", "source_date", "rows")
#: Слово поля, слово «пустое» в его роде и предел длины.
_TEXTS: Final = {
    "block": ("блок", "пустой", NAME_MAX),
    "name": ("имя", "пустое", NAME_MAX),
    "norm": ("норма", "пустая", TEXT_MAX),
    "error": ("ошибка", "пустая", TEXT_MAX),
    "recommendation": ("рекомендация", "пустая", TEXT_MAX),
}
_ENUMS: Final[dict[str, tuple[str, type[StrEnum]]]] = {
    "method": ("способ", Method),
    "coverage": ("охват", Coverage),
    "importance": ("важность", Importance),
    "scope": ("область", Scope),
}
_SCOPES: Final = frozenset(Scope)
_UNITS: Final = frozenset(Unit)
_NEEDS_SHARE: Final = {
    Scope.PAGES: "у проверки по всем страницам нужна доля",
    Scope.SAMPLE: "у выборки нужна доля",
}

Text = Annotated[str, StringConstraints(min_length=1, max_length=TEXT_MAX)]
Name = Annotated[str, StringConstraints(min_length=1, max_length=NAME_MAX)]


class ParamIn(BaseModel):
    """Порог строки в файле: имя, значение, единица."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    name: str
    value: Decimal
    unit: Unit = Field(strict=False)


class RowIn(BaseModel):
    """Строка справочника в файле; перечисления — строкой, остальное — строго."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    code: str
    block: Name
    name: Name
    method: Method = Field(strict=False)
    coverage: Coverage = Field(strict=False)
    norm: Text
    error: Text
    recommendation: Text
    importance: Importance = Field(strict=False)
    scope: Scope = Field(strict=False)
    share_percent: Decimal | None
    params: list[ParamIn]


@dataclass(frozen=True)
class ReferenceFile:
    """Годный файл: sha256 его байтов, дата снимка, строки по порядку."""

    sha256: str
    source_date: date
    rows: tuple[RowIn, ...]


class FileRefusedError(ValueError):
    """Файл негоден: все причины за прогон, по строке на каждую."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("\n".join(reasons))
        self.reasons = reasons


class _ConstantError(ValueError):
    """`NaN` или `Infinity` в тексте JSON."""


class _Object(dict[str, Any]):
    """Объект JSON с его повторёнными ключами: причину называет тот, кто разбирает объект."""

    repeated: tuple[str, ...] = ()


def _pairs(pairs: list[tuple[str, Any]]) -> _Object:
    """Повтор ключа объекта — отказ, а не «побеждает последний» (строгая схема З46)."""
    found = _Object(pairs)
    found.repeated = tuple(key for key, count in Counter(k for k, _ in pairs).items() if count > 1)
    return found


def read(stream: IO[bytes]) -> bytes:
    """Байты файла — не больше предела и одного байта сверх: больше — дальше не читается (A306)."""
    data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise FileRefusedError(["файл больше 1 МБ — дальше не читается"])
    return data


def _constant(name: str) -> None:
    raise _ConstantError(name)


def _decode(data: bytes) -> object:
    """Текст JSON в объекты: числа — `Decimal`, место ошибки разбора — строкой и знаком."""
    if data.startswith(b"\xef\xbb\xbf"):
        raise FileRefusedError(["в начале файла метка BOM — сохраните файл без неё"])
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise FileRefusedError(["файл не в UTF-8"]) from None
    try:
        return json.loads(
            text,
            parse_float=Decimal,
            parse_int=Decimal,
            parse_constant=_constant,
            object_pairs_hook=_pairs,
        )
    except _ConstantError:
        raise FileRefusedError(["в файле NaN или Infinity — числа только конечные"]) from None
    except json.JSONDecodeError as bad:
        raise FileRefusedError([f"файл — не JSON: строка {bad.lineno}, знак {bad.colno}"]) from None


def parse(data: bytes) -> ReferenceFile:
    """Годный файл или отказ со всеми причинами: сначала верхний уровень, затем строки по
    порядку; строки проверяются, если их от 1 до предела."""
    doc = _decode(data)
    if not isinstance(doc, dict):
        raise FileRefusedError(["файл — не объект с полями format, source_date и rows"])
    reasons = [f"повторяется поле{_key(key)}" for key in getattr(doc, "repeated", ())]
    reasons += [f"лишнее поле верхнего уровня{_key(key)}" for key in doc if key not in _TOP]
    reasons += [f"нет поля {key}" for key in _TOP if key not in doc]
    if "format" in doc and not _is_format(doc["format"]):
        reasons.append(f"формат{_number(doc['format'])} не поддерживается — нужен {FORMAT}")
    day = _day(doc.get("source_date"))
    if "source_date" in doc and day is None:
        reasons.append("дата снимка — вида 2026-11-18")
    rows = doc.get("rows")
    if "rows" in doc and not isinstance(rows, list):
        reasons.append("rows — нужен список строк")
    elif isinstance(rows, list) and not rows:
        reasons.append("в файле нет строк")
    elif isinstance(rows, list) and len(rows) > MAX_ROWS:
        reasons.append(f"строк больше {MAX_ROWS}")
    found: list[RowIn] = []
    if isinstance(rows, list) and len(rows) <= MAX_ROWS:
        seen: dict[str, int] = {}
        for n, raw in enumerate(rows, 1):
            row, why = _row(n, raw, seen)
            reasons += why
            found += [row] if row else []
    if reasons or day is None:
        raise FileRefusedError(reasons)
    return ReferenceFile(hashlib.sha256(data).hexdigest(), day, tuple(found))


def _day(value: object) -> date | None:
    """Дата снимка — `ГГГГ-ММ-ДД` цифрами ASCII и настоящая дата; иначе None."""
    if not (isinstance(value, str) and _DATE.fullmatch(value)):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _is_format(value: object) -> bool:
    return type(value) is Decimal and value == FORMAT


def _number(value: object) -> str:
    """Номер формата в тексте отказа — только небольшое целое: иное не печатается."""
    small = type(value) is Decimal and value == value.to_integral_value() and 0 <= value < 1000
    return f" {value:f}" if small else ""


def _key(key: str) -> str:
    """Имя лишнего поля — только латиницей: иное может оказаться текстом файла (A307)."""
    return f": {key}" if _NAME.fullmatch(key) else " с именем не латиницей"


def _row(n: int, raw: object, seen: dict[str, int]) -> tuple[RowIn | None, list[str]]:
    """Строка или её причины по порядку: код, повтор ключа, булево на месте числа, схема, правила
    строки — и у строки с ошибкой схемы, по полям годного типа (решение ведущей 2026-11-26)."""
    if not isinstance(raw, dict):
        return None, [f"строка {n} — не объект"]
    code = raw.get("code")
    if isinstance(code, str) and _CODE.fullmatch(code):
        where = f"строка {n}, код {code}"
        why = [f"{where}: код уже был в строке {seen[code]}"] if code in seen else []
        seen.setdefault(code, n)
    else:
        where, why = f"строка {n}", [f"строка {n}: код — номер вида 7 или 1.3"]
    reasons = [f"повторяется поле{_key(key)}" for key in getattr(raw, "repeated", ())]
    reasons += _booleans(raw)
    row = None
    try:
        row = RowIn.model_validate(raw)
    except ValidationError as invalid:
        errors = invalid.errors(include_input=False, include_url=False)
        reasons += [word for e in errors if (word := _word(e["loc"], e["type"], raw))]
    why += [f"{where}: {reason}" for reason in reasons + _rules(raw)]
    return (None if why else row), why


def _booleans(raw: dict[str, Any]) -> list[str]:
    """`true` и `false` на месте числа: в Python `True == 1`, схема назвала бы их «не числом»."""
    share = raw.get("share_percent")
    found = ["доля — число, а не true или false"] if isinstance(share, bool) else []
    params = raw.get("params")
    for i, param in enumerate(params if isinstance(params, list) else [], 1):
        if isinstance(param, dict) and isinstance(param.get("value"), bool):
            found.append(f"{_label(param, i)} — число, а не true или false")
    return found


def _label(param: object, i: int) -> str:
    """Имя порога, если оно годно; иначе — его место в строке."""
    name = param.get("name") if isinstance(param, dict) else None
    return name if isinstance(name, str) and _NAME.fullmatch(name) else f"порог {i}"


def _choices(enum: type[StrEnum]) -> str:
    values = [member.value for member in enum]
    return f"{', '.join(values[:-1])} или {values[-1]}"


def _word(loc: tuple[int | str, ...], kind: str, raw: dict[str, Any]) -> str | None:
    """Ошибка схемы строки своими словами — по месту и виду, без значения; уже названное — None:
    причину кода и булева на месте числа строка назвала до схемы."""
    field = str(loc[0])
    if field == "code":
        return None
    if field == "params":
        return _param_word(loc[1:], kind, raw["params"]) if loc[1:] else "пороги — нужен список"
    if kind in ("missing", "extra_forbidden"):
        return _field_word(field, kind)
    if field in _TEXTS:
        return _text_word(field, kind)
    if field in _ENUMS:
        word, enum = _ENUMS[field]
        return f"{word} — {_choices(enum)}"
    return None if isinstance(raw.get(field), bool) else "доля — нужно число"


def _field_word(field: str, kind: str) -> str:
    return f"нет поля {field}" if kind == "missing" else f"лишнее поле{_key(field)}"


def _text_word(field: str, kind: str) -> str:
    word, empty, limit = _TEXTS[field]
    words = {
        "string_too_short": f"{word} {empty}",
        "string_too_long": f"{word} длиннее {limit} знаков",
    }
    return words.get(kind, f"{word} — нужен текст")


def _param_word(loc: tuple[int | str, ...], kind: str, params: list[Any]) -> str | None:
    i = int(loc[0])
    label = _label(params[i], i + 1)
    if len(loc) == 1:
        return f"{label} — нужен объект"
    field = str(loc[1])
    if kind in ("missing", "extra_forbidden"):
        return f"{label}: {_field_word(field, kind)}"
    if field == "unit":
        return f"{label}: единица — {_choices(Unit)}"
    if field == "value":
        return None if isinstance(params[i].get("value"), bool) else f"{label} — нужно число"
    return f"порог {i + 1}: имя — нужен текст"


def _rules(raw: dict[str, Any]) -> list[str]:
    """Правила строки поверх схемы — по полям годного типа: правило на негодном поле молчит, без
    каскада ложных причин. Порядок: доля и область, нулевой знак в текстах, пороги."""
    why = _share_rules(raw.get("scope"), raw.get("share_percent"))
    for field, (word, _, _) in _TEXTS.items():
        if isinstance(raw.get(field), str) and "\x00" in raw[field]:
            why.append(f"{word} — в тексте нулевой знак: база такой текст не примет")
    params = raw.get("params")
    return why + (_param_rules(params) if isinstance(params, list) else [])


def _share_rules(scope: object, share: object) -> list[str]:
    """У «сайта» доли нет, у страниц и выборки она есть — больше 0 и не больше 100."""
    if not (share is None or isinstance(share, Decimal)):
        return []  # не число: причину назвали схема или проверка булевых
    why = []
    if isinstance(scope, str) and scope in _SCOPES:
        if scope == Scope.SITE and share is not None:
            why.append("доля — только у проверок по страницам и выборке")
        if scope != Scope.SITE and share is None:
            why.append(_NEEDS_SHARE[Scope(scope)])
    if isinstance(share, Decimal) and not Decimal(0) < share <= 100:
        why.append("доля — больше 0 и не больше 100")
    return why


def _param_rules(params: list[Any]) -> list[str]:
    """Пороги по полям годного типа: повтор ключа, имя, повтор имени, пределы единицы."""
    why: list[str] = []
    names: set[str] = set()
    for i, param in enumerate(params, 1):
        if not isinstance(param, dict):
            continue  # вид порога назвала схема
        repeated = getattr(param, "repeated", ())
        why += [f"{_label(param, i)}: повторяется поле{_key(key)}" for key in repeated]
        name, value, unit = param.get("name"), param.get("value"), param.get("unit")
        if not isinstance(name, str):
            continue
        if not _NAME.fullmatch(name):
            why.append(f"порог {i}: имя — латиница a–z, цифры и _, с буквы, до 40 знаков")
            continue
        if name in names:
            why.append(f"порог {name} повторяется")
        names.add(name)
        if isinstance(value, Decimal) and isinstance(unit, str) and unit in _UNITS:
            low, high = UNITS[Unit(unit)]
            if not low <= value <= high:
                why.append(f"{name} вне пределов единицы {unit}: {low}–{high}")
    return why
