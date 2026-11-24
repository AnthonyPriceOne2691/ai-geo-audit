"""Реестр ключей и настроек: имя в базе, слово для человека и вид значения (A230).

Новая строка реестра приходит вместе со своим потребителем; таблица реестра в тесте держит
каждую строку поимённо. Слова ключей — как в ТЗ («Сервисы и затраты»).
"""

from __future__ import annotations

from dataclasses import dataclass

#: Вид значения ключа: одна строка без пробелов, уходит в заголовок запроса провайдеру.
SINGLE_LINE = "токен"


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
