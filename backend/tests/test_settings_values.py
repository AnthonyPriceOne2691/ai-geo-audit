"""Обычные настройки в базе (A232–A235, A238): новая версия, а не правка; повтор — без записи;
две записи одной версии разом; негодное в базе — громкий отказ; список стран и языков."""

from __future__ import annotations

import asyncio
import json
import logging
import string

import dbtools
import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from ai_geo_audit.settings import registry, values
from ai_geo_audit.settings.values import Versioned

#: Таблица реестра настроек — по строке на настройку, без числа строк (A232, L37): новая строка
#: реестра без строки здесь — красное. Границы `audits_parallel` держат A233 и соседние случаи.
REGISTRY = [
    ("audits_parallel", "Аудитов одновременно", 3),
    ("audit_locales", "Страны и языки аудита", []),
]
WORDS = {name: word for name, word, _ in REGISTRY}
PARALLEL = "Аудитов одновременно — целое от 1 до 10"
COUNTRY = "Страны и языки аудита — страна — две латинские буквы ISO 3166-1"
LANGUAGE = "Страны и языки аудита — язык — две латинские буквы ISO 639-1"
PAIRS = "Страны и языки аудита — нужен список пар"
REPEATED = "Страны и языки аудита — пара повторяется"
ROWS = text("SELECT name, version, value FROM setting_versions ORDER BY name, version")
#: Строка версии мимо `put_setting` — как правка базы руками (A235).
RAW = text(
    "INSERT INTO setting_versions (name, version, value) VALUES (:name, 1, CAST(:value AS jsonb))"
)
#: Строка с номером, который счётчик `id` выдаст следующим: вставку версии роняет ключ строки.
TAKEN = text(
    "INSERT INTO setting_versions (id, name, version, value) VALUES "
    "(nextval(pg_get_serial_sequence('setting_versions', 'id')) + 1, 'other', 1, '1')"
)


async def rows(target: AsyncEngine | AsyncSession) -> list[tuple[object, ...]]:
    """Строки версий: у движка — зафиксированные, у сессии — видимые ей."""
    if isinstance(target, AsyncSession):
        return [tuple(row) for row in await target.execute(ROWS)]
    async with target.connect() as conn:
        return [tuple(row) for row in await conn.execute(ROWS)]


def pairs(count: int) -> list[list[str]]:
    """`count` разных годных пар: страны AA, AB, … с немецким языком."""
    letters = string.ascii_uppercase
    return [[first + second, "de"] for first in letters for second in letters][:count]


def test_a232_setting_registry_is_the_table() -> None:
    """A232: реестр настроек совпадает с таблицей теста — имена, слова и умолчания."""
    assert [(spec.name, spec.word, spec.default) for spec in registry.SETTINGS] == REGISTRY


async def test_a232_each_change_is_a_new_version_and_a_repeat_writes_nothing(
    session: AsyncSession,
) -> None:
    """A232: без строк — 3, версия 0; 5 — версия 1; ещё раз 5 — без записи, версия 1; 4 с
    `expected=1` — версия 2; снова 4 с `expected=1` — без записи и без отказа, версия 2. Запись
    от умолчания, `expected=0`, при версии 2 — отказ до записи (находка 10 второй проверяющей)."""
    assert await values.current(session, "audits_parallel") == Versioned(3, 0)
    got = [
        await values.put_setting(session, "audits_parallel", value, expected=expected)
        for value, expected in [(5, None), (5, None), (4, 1), (4, 1)]
    ]
    assert got == [Versioned(5, 1), Versioned(5, 1), Versioned(4, 2), Versioned(4, 2)]
    with pytest.raises(values.SettingChanged) as stale:
        await values.put_setting(session, "audits_parallel", 6, expected=0)
    assert stale.value.args == ("настройку уже изменили: версия 2 — перечитайте",)
    assert await values.current(session, "audits_parallel") == Versioned(4, 2)
    assert await rows(session) == [("audits_parallel", 1, 5), ("audits_parallel", 2, 4)]


async def test_a232_default_is_not_written_and_true_in_the_base_is_not_one(
    session: AsyncSession,
) -> None:
    """A232, соседние случаи: без строк запись умолчания 3 — без записи, версия 0. `true` в базе —
    не 1: запись 1 поверх такой строки — новая версия, и `current` снова годен."""
    assert await values.put_setting(session, "audits_parallel", 3) == Versioned(3, 0)
    assert await rows(session) == []
    await session.execute(RAW, {"name": "audits_parallel", "value": "true"})
    assert await values.put_setting(session, "audits_parallel", 1) == Versioned(1, 2)
    assert await values.current(session, "audits_parallel") == Versioned(1, 2)


REFUSED = {"zero": 0, "eleven": 11, "digit-string": "3", "true": True, "fraction": 3.5}


@pytest.mark.parametrize("case", list(REFUSED))
async def test_a233_bad_parallel_is_refused_before_any_write(
    session: AsyncSession, case: str
) -> None:
    """A233: 0, 11, строка «3», логическое «да», 3,5 — `SettingRefused` «Аудитов одновременно —
    целое от 1 до 10»: строка и «да» — отказ, а не 3 и 1. Таблица не тронута."""
    with pytest.raises(values.SettingRefused) as refused:
        await values.put_setting(session, "audits_parallel", REFUSED[case])
    assert refused.value.args == (PARALLEL,)
    assert await rows(session) == []


async def test_a233_bounds_are_written_and_an_unknown_name_is_refused(
    session: AsyncSession,
) -> None:
    """A233, соседние случаи: 1 и 10 записываются; имя `nope` — «nope — такой настройки нет» и у
    записи, и у чтения."""
    assert await values.put_setting(session, "audits_parallel", 1) == Versioned(1, 1)
    assert await values.put_setting(session, "audits_parallel", 10) == Versioned(10, 2)
    with pytest.raises(values.SettingRefused) as read:
        await values.current(session, "nope")
    with pytest.raises(values.SettingRefused) as written:
        await values.put_setting(session, "nope", 3)
    assert [read.value.args, written.value.args] == [("nope — такой настройки нет",)] * 2


async def test_a234_two_writes_of_one_version_at_once_leave_one(committed: AsyncEngine) -> None:
    """A234: при версии 1 две сессии с барьером пишут 4 и 6, обе с `expected=1`. Соединения и
    транзакции открыты до барьера: обе читают версию 1, вторая вставка ждёт замка первой. Прошла
    одна — версия 2; другая — `SettingChanged` «настройку уже изменили: версия 2 — перечитайте»
    от ограничения `uq_setting_versions_name_version`: его ошибка — в `__context__`. Сессия
    отказа жива и читает версию 2. Запись 7 с `expected=1` при версии 2 — тот же отказ до записи:
    ошибки базы в `__context__` нет."""
    async with AsyncSession(committed) as session:
        await values.put_setting(session, "audits_parallel", 5)
        await session.commit()
    barrier = asyncio.Barrier(2)
    written: list[int] = []
    refused: list[tuple[tuple[object, ...], BaseException | None, Versioned]] = []

    async def write(value: int) -> None:
        async with AsyncSession(committed) as session:
            await session.connection()
            await dbtools.meet(barrier)
            try:
                await values.put_setting(session, "audits_parallel", value, expected=1)
            except values.SettingChanged as changed:
                seen = await values.current(session, "audits_parallel")
                refused.append((changed.args, changed.__context__, seen))
                return
            await session.commit()
            written.append(value)

    await asyncio.gather(write(4), write(6))
    [winner], [(args, context, seen)] = written, refused
    assert args == ("настройку уже изменили: версия 2 — перечитайте",)
    assert isinstance(context, IntegrityError)
    assert context.orig.__cause__.constraint_name == values.SAME_VERSION  # type: ignore[union-attr]
    assert seen == Versioned(winner, 2)
    async with AsyncSession(committed) as session:
        with pytest.raises(values.SettingChanged) as stale:
            await values.put_setting(session, "audits_parallel", 7, expected=1)
    assert (stale.value.args, stale.value.__context__) == (args, None)
    assert await rows(committed) == [("audits_parallel", 1, 5), ("audits_parallel", 2, winner)]


async def test_a234_another_constraint_is_not_taken_for_a_changed_setting(
    session: AsyncSession,
) -> None:
    """A234, соседний случай: вставку версии роняет другое ограничение — ключ строки
    `pk_setting_versions` занят строкой мимо счётчика. Отказа «уже изменили» нет: наружу идёт
    ошибка базы со своим именем ограничения."""
    await session.execute(TAKEN)
    with pytest.raises(IntegrityError) as error:
        await values.put_setting(session, "audits_parallel", 5)
    assert error.value.orig.__cause__.constraint_name == "pk_setting_versions"  # type: ignore[union-attr]


@pytest.mark.parametrize("shape", ["no-driver-error", "no-cause"])
def test_a234_an_error_without_a_constraint_name_is_named_none(shape: str) -> None:
    """A234, соседний случай: у ошибки базы нет ошибки драйвера или у той нет причины с именем
    ограничения. Имя — None: `put_setting` отдаёт ошибку как есть, а не «уже изменили» и не
    `AttributeError`."""
    error = IntegrityError("INSERT INTO setting_versions", None, ValueError("без причины"))
    if shape == "no-driver-error":
        error.orig = None
    assert values._constraint(error) is None


#: Значение в базе — текст отказа — что не так (в записи журнала — без значения).
INVALID = {
    "out-of-range": (
        "audits_parallel",
        50,
        "Аудитов одновременно: в базе 50 — вне 1–10",
        "вне 1–10",
    ),
    "true": ("audits_parallel", True, "Аудитов одновременно: в базе true — не целое", "не целое"),
    "digit-string": (
        "audits_parallel",
        "3",
        'Аудитов одновременно: в базе "3" — не целое',
        "не целое",
    ),
    "repeated-pair": (
        "audit_locales",
        [["DE", "de"], ["de", "DE"]],
        'Страны и языки аудита: в базе [["DE", "de"], ["de", "DE"]] — пара повторяется',
        "пара повторяется",
    ),
    "shown-whole": (
        "audits_parallel",
        "x" * 58,
        f'Аудитов одновременно: в базе "{"x" * 58}" — не целое',
        "не целое",
    ),
    "shown-cut": (
        "audits_parallel",
        "x" * 59,
        f'Аудитов одновременно: в базе "{"x" * 58}… — не целое',
        "не целое",
    ),
}


@pytest.mark.parametrize("case", list(INVALID))
async def test_a235_bad_value_in_the_base_is_refused_loudly(
    session: AsyncSession, caplog: pytest.LogCaptureFixture, case: str
) -> None:
    """A235: строка версии вставлена в базу мимо `put_setting` — `current` отдаёт
    `SettingInvalid` со значением из базы JSON-текстом до 60 знаков и пишет ERROR логгера
    `ai_geo_audit.settings`: `event` — `setting-invalid`, `setting` — имя; значения в записи нет.
    Умолчанием не подменяется."""
    name, raw, message, found = INVALID[case]
    await session.execute(RAW, {"name": name, "value": json.dumps(raw)})
    caplog.set_level(logging.ERROR, logger="ai_geo_audit.settings")
    with pytest.raises(values.SettingInvalid) as invalid:
        await values.current(session, name)
    assert invalid.value.args == (message,)
    [record] = [r for r in caplog.records if r.name == "ai_geo_audit.settings"]
    fields = (record.levelname, record.event, record.setting)  # type: ignore[attr-defined]
    assert fields == ("ERROR", "setting-invalid", name)
    assert record.getMessage() == f"настройка в базе не годится: {WORDS[name]} — {found}"
    logged = record.exc_info[1] if record.exc_info else None
    assert isinstance(logged, registry.Unfit) and str(logged) == logged.need


async def test_a238_codes_are_brought_to_case_and_a_case_repeat_writes_nothing(
    session: AsyncSession,
) -> None:
    """A238: без строк — пустой список, версия 0; Германия и немецкий, США и английский кодами в
    разном регистре — версия 1, страна заглавными, язык строчными. Тот же список в другом регистре
    — без новой версии (п. 8 проверяющей). Умолчание — свой список на вызов: правка отданного
    реестр не трогает. Рядом строка `audits_parallel`: версий настройки не делят (находка 11)."""
    await values.put_setting(session, "audits_parallel", 5)
    empty = await values.current(session, "audit_locales")
    assert empty == Versioned([], 0)
    assert isinstance(empty.value, list)
    empty.value.append(["DE", "de"])
    assert await values.current(session, "audit_locales") == Versioned([], 0)
    written = await values.put_setting(session, "audit_locales", [["de", "DE"], ["Us", "eN"]])
    assert written == Versioned([["DE", "de"], ["US", "en"]], 1)
    again = await values.put_setting(session, "audit_locales", [["dE", "De"], ["us", "EN"]])
    assert again == written
    assert await values.current(session, "audit_locales") == written
    assert await rows(session) == [("audit_locales", 1, written.value), ("audits_parallel", 1, 5)]


async def test_a238_three_hundred_pairs_are_written(session: AsyncSession) -> None:
    """A238, соседний случай: ровно 300 разных пар — версия 1."""
    many = pairs(300)
    assert await values.put_setting(session, "audit_locales", many) == Versioned(many, 1)


LOCALES_REFUSED = {
    "three-letter-country": ([["DEU", "de"]], COUNTRY),
    "one-letter-country": ([["D", "de"]], COUNTRY),
    "number-country": ([[49, "de"]], COUNTRY),
    "digit-in-language": ([["DE", "d1"]], LANGUAGE),
    "three-letter-language": ([["DE", "deu"]], LANGUAGE),
    "number-language": ([["DE", 49]], LANGUAGE),
    "repeated-pair": ([["DE", "de"], ["US", "en"], ["de", "DE"]], REPEATED),
    "not-a-list": ("DE de", PAIRS),
    "object": ({"DE": "de"}, PAIRS),
    "flat-list": (["DE", "de"], PAIRS),
    "pair-of-one": ([["DE"]], PAIRS),
    "pair-of-three": ([["DE", "de", "en"]], PAIRS),
    "too-many": (pairs(301), "Страны и языки аудита — не больше 300 пар"),
}


@pytest.mark.parametrize("case", list(LOCALES_REFUSED))
async def test_a238_bad_locales_are_refused_before_any_write(
    session: AsyncSession, case: str
) -> None:
    """A238: страна из трёх букв, язык с цифрой, повтор пары после приведения, не список, 301 пара
    и соседние случаи — `SettingRefused` «Страны и языки аудита — …». Таблица не тронута."""
    value, message = LOCALES_REFUSED[case]
    with pytest.raises(values.SettingRefused) as refused:
        await values.put_setting(session, "audit_locales", value)
    assert refused.value.args == (message,)
    assert await rows(session) == []


#: Не латиница ASCII, которую приведение регистра или NFKC делает латиницей (п. 7 проверяющей):
#: проверка — до приведения. Знаки — кодами: так их видно в исходнике.
LOOKALIKES = {
    "cyrillic": "\u0414\u0415",  # кириллические Д и Е
    "fullwidth": "\uff24\uff25",  # полноширинные D и E: NFKC даёт DE
    "long-s": "\u017fK",  # длинное s и K: upper даёт SK
    "sharp-s": "\u00df",  # эсцет: upper даёт SS
    "ligature": "\ufb00",  # лигатура ff: upper даёт FF
    "dotless-i": "\u0131t",  # i без точки и t: upper даёт IT
    "kelvin": "\u212ae",  # знак кельвина и e: lower даёт ke
}


@pytest.mark.parametrize("lookalike", list(LOOKALIKES))
@pytest.mark.parametrize("place", ["country", "language"])
async def test_a238_lookalike_letters_are_refused_before_the_case_is_brought(
    session: AsyncSession, place: str, lookalike: str
) -> None:
    """A238, п. 7 проверяющей: код из знаков, что после `upper`, `lower` или NFKC стали бы
    латиницей, — отказ своего места пары; таблица не тронута."""
    code = LOOKALIKES[lookalike]
    pair, message = ([code, "de"], COUNTRY) if place == "country" else (["DE", code], LANGUAGE)
    with pytest.raises(values.SettingRefused) as refused:
        await values.put_setting(session, "audit_locales", [pair])
    assert refused.value.args == (message,)
    assert await rows(session) == []
