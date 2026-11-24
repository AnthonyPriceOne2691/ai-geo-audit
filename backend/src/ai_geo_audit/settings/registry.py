"""Реестр ключей и настроек: имя в базе, слово для человека и вид значения (A230).

Новая строка реестра приходит вместе со своим потребителем; таблица реестра в тесте держит
каждую строку поимённо. Слова ключей — как в ТЗ («Сервисы и затраты»).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

#: Вид значения ключа: одна строка без пробелов, уходит в заголовок запроса провайдеру.
SINGLE_LINE = "токен"
#: Короче маска журнала значение не прячет (`logs.register_secret`), длиннее — не токен (A225).
KEY_MIN_CHARS = 6
KEY_MAX_BYTES = 512
#: Пробел и переводы строки ASCII: по краям — «уберите», внутри — отказ.
_BLANKS = frozenset(" \t\n\r\x0b\x0c")
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
