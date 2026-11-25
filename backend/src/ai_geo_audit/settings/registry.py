"""Реестр ключей и настроек: имя в базе, слово для человека и вид значения (A230).

Новая строка реестра приходит вместе со своим потребителем; таблица реестра в тесте держит
каждую строку поимённо. Слова ключей — как в ТЗ («Сервисы и затраты»).
"""

from __future__ import annotations

import string
from collections.abc import Callable
from dataclasses import dataclass

#: Вид значения ключа: одна строка без пробелов, уходит в заголовок запроса провайдеру.
SINGLE_LINE = "токен"
#: Короче маска журнала значение не прячет (`logs.register_secret`), длиннее — не токен (A225).
KEY_MIN_CHARS = 6
KEY_MAX_BYTES = 512
#: Пробел, табуляция и переводы строки — `\n` и `\r`, как в CRLF (A220): по краям — «уберите»,
#: внутри — отказ. Вертикальная табуляция и перевод страницы — управляющие знаки, как нулевой и
#: `ESC`: их ловит правило видимых знаков.
_BLANKS = frozenset(" \t\n\r")
#: Видимые знаки ASCII — от `!` до `~`: ни нулевого, ни `ESC`, ни `DEL` (слово владельца A225).
_VISIBLE = frozenset(chr(code) for code in range(0x21, 0x7F))


@dataclass(frozen=True, slots=True)
class KeySpec:
    """Ключ внешнего сервиса: имя строки в базе, слово для человека и вид значения."""

    name: str
    word: str
    kind: str


KEYS: tuple[KeySpec, ...] = (
    KeySpec("openai", "OpenAI", SINGLE_LINE),
    KeySpec("anthropic", "Claude", SINGLE_LINE),
    KeySpec("gemini", "Gemini", SINGLE_LINE),
    KeySpec("perplexity", "Perplexity", SINGLE_LINE),
    KeySpec("ahrefs", "Ahrefs", SINGLE_LINE),
    KeySpec("pagespeed", "PageSpeed Insights", SINGLE_LINE),
)
_KEYS = {spec.name: spec for spec in KEYS}


def key(name: str) -> KeySpec | None:
    """Строка реестра ключа по имени; такого ключа нет — None."""
    return _KEYS.get(name)


def key_word(name: str) -> str:
    """Слово ключа для текста отказа; имени нет в реестре — само имя."""
    spec = key(name)
    return spec.word if spec else name


#: Правила значения ключа по порядку — от явного к общему: пробел внутри — «без пробелов», а не
#: «только видимые знаки». Первое нарушенное даёт причину (A225); пустое значение — первым, до
#: взгляда на края.
_KEY_RULES: tuple[tuple[Callable[[str], bool], str], ...] = (
    (lambda value: not value, "пусто"),
    (
        lambda value: value[0] in _BLANKS or value[-1] in _BLANKS,
        "в начале или в конце пробел или перевод строки — уберите",
    ),
    (lambda value: not _BLANKS.isdisjoint(value), "без пробелов и переводов строки"),
    (
        lambda value: not _VISIBLE.issuperset(value),
        "только видимые знаки ASCII: значение уходит в заголовок запроса",
    ),
    (lambda value: len(value) < KEY_MIN_CHARS, "короче 6 знаков — маска журнала его не спрячет"),
    (lambda value: len(value.encode()) > KEY_MAX_BYTES, "длиннее 512 байт"),
)


def key_problem(value: str) -> str | None:
    """Причина отказа значения ключа словами, без значения; годно — None (A225)."""
    return next((reason for broken, reason in _KEY_RULES if broken(value)), None)


#: Пределы обычных настроек: аудитов одновременно — 1–10 (ТЗ:66, :214), пар — до 300 (A238).
PARALLEL_MIN, PARALLEL_MAX = 1, 10
LOCALES_MAX = 300
PARALLEL_NEED = f"целое от {PARALLEL_MIN} до {PARALLEL_MAX}"
COUNTRY_NEED = "страна — две латинские буквы ISO 3166-1"
LANGUAGE_NEED = "язык — две латинские буквы ISO 639-1"
PAIRS_NEED = "нужен список пар"
#: Латинские буквы ASCII: проверка — до приведения регистра, иначе `str.upper` делает из `ß`, `ſ`
#: и `ı` латинские буквы.
_LATIN = frozenset(string.ascii_letters)


class Unfit(ValueError):  # noqa: N818 -- причина отказа, а не сбой: её текст уходит в отказ
    """Значение настройки не годится: `need` — что нужно, для отказа записи (A233, A238);
    `found` — что не так со значением в базе (A235). Значения в тексте нет."""

    def __init__(self, need: str, found: str | None = None) -> None:
        super().__init__(need)
        self.need = need
        self.found = found or need


@dataclass(frozen=True, slots=True)
class SettingSpec:
    """Обычная настройка: имя в базе, слово для человека, умолчание и проверка значения —
    приведённое значение или `Unfit`."""

    name: str
    word: str
    default: object
    fit: Callable[[object], object]


def _parallel(value: object) -> int:
    """Целое от 1 до 10: логическое «да» — не 1, строка «3» — не 3 (A233)."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise Unfit(PARALLEL_NEED, "не целое")
    if not PARALLEL_MIN <= value <= PARALLEL_MAX:
        raise Unfit(PARALLEL_NEED, f"вне {PARALLEL_MIN}–{PARALLEL_MAX}")
    return value


def _code(code: object, case: Callable[[str], str], need: str) -> str:
    """Код ISO — ровно две латинские буквы ASCII; регистр приводится после проверки."""
    if not isinstance(code, str) or len(code) != 2 or not _LATIN.issuperset(code):
        raise Unfit(need)
    return case(code)


def _locales(value: object) -> list[list[str]]:
    """Список пар «страна — язык»: страна заглавными, язык строчными; повтор пары после
    приведения — отказ, не больше 300 пар (A238)."""
    if not isinstance(value, list):
        raise Unfit(PAIRS_NEED)
    if len(value) > LOCALES_MAX:
        raise Unfit(f"не больше {LOCALES_MAX} пар")
    pairs: list[list[str]] = []
    seen: set[tuple[str, str]] = set()
    for pair in value:
        if not isinstance(pair, list) or len(pair) != 2:
            raise Unfit(PAIRS_NEED)
        country = _code(pair[0], str.upper, COUNTRY_NEED)
        language = _code(pair[1], str.lower, LANGUAGE_NEED)
        if (country, language) in seen:
            raise Unfit("пара повторяется")
        seen.add((country, language))
        pairs.append([country, language])
    return pairs


SETTINGS: tuple[SettingSpec, ...] = (
    SettingSpec("audits_parallel", "Аудитов одновременно", 3, _parallel),
    SettingSpec("audit_locales", "Страны и языки аудита", [], _locales),
)
_SETTINGS = {spec.name: spec for spec in SETTINGS}


def setting(name: str) -> SettingSpec | None:
    """Строка реестра настройки по имени; такой настройки нет — None."""
    return _SETTINGS.get(name)
