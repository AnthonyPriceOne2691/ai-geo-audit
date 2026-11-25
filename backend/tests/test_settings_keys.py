"""Ключи сервисов в базе (A223–A231, A237): запись шифротекстом, снятие, резолвер в момент вызова,
состояние для экрана, гонки на двух соединениях; значения нет ни в базе, ни в выводе."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import re
from collections.abc import Callable
from pathlib import Path

import dbtools
import leaks
import masterkeys
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from ai_geo_audit.logs import setup as logs
from ai_geo_audit.settings import crypto, keys, registry
from ai_geo_audit.settings.models import ServiceKey

pytestmark = pytest.mark.usefixtures("master_file")

#: Таблица реестра ключей — по строке на ключ, без числа строк (A230, L37): новая строка реестра
#: без строки здесь — красное.
REGISTRY = [
    ("openai", "OpenAI", "токен"),
    ("anthropic", "Claude", "токен"),
    ("gemini", "Gemini", "токен"),
    ("perplexity", "Perplexity", "токен"),
    ("ahrefs", "Ahrefs", "токен"),
    ("pagespeed", "PageSpeed Insights", "токен"),
]
DUMP = text(
    "SELECT name, ciphertext, master_id, fingerprint, hint, set_at::text AS set_at "
    "FROM service_keys ORDER BY name"
)
ROW = text("SELECT ciphertext, master_id, fingerprint, set_at FROM service_keys WHERE name = :name")
#: Перестановка строк двух ключей местами (подмена в базе, «Безопасность», п. 4).
SWAP = text(
    "UPDATE service_keys AS k SET ciphertext = o.ciphertext, master_id = o.master_id, "
    "fingerprint = o.fingerprint, hint = o.hint FROM service_keys AS o "
    "WHERE k.name IN ('openai', 'ahrefs') AND o.name IN ('openai', 'ahrefs') AND k.name <> o.name"
)


async def count(target: AsyncEngine | AsyncSession) -> int:
    """Строк в таблице ключей: у движка — зафиксированных, у сессии — видимых ей."""
    if isinstance(target, AsyncSession):
        return (await target.execute(text("SELECT count(*) FROM service_keys"))).scalar_one()
    async with target.connect() as conn:
        return (await conn.execute(text("SELECT count(*) FROM service_keys"))).scalar_one()


def shown(*items: object) -> str:
    """Что выйдет наружу из итогов и состояний: `repr` и JSON."""
    plain = [dataclasses.asdict(item) for item in items]  # type: ignore[call-overload]
    return repr(items) + json.dumps(plain, default=str)


def test_a230_key_registry_is_the_table() -> None:
    """A230: реестр ключей совпадает с таблицей теста — имена, слова и вид по строке на ключ."""
    assert [(spec.name, spec.word, spec.kind) for spec in registry.KEYS] == REGISTRY


async def test_a224_rows_hold_only_ciphertext_and_put_key_does_not_commit(
    engine: AsyncEngine,
) -> None:
    """A224: `put_key` для `openai` — метка из 51 знака, для `pagespeed` — из 12; дамп строк
    текстом: шифротекст, номер мастер-ключа, отпечаток из 12 знаков hex, время. Подсказка — 4
    последних знака у первой, у второй пусто; метки нет ни в одной колонке; итог `put_key` —
    состояние без значения. Фиксации внутри нет: после отката сессии строк 0."""
    long, short = masterkeys.marker(51), masterkeys.marker(12)
    async with AsyncSession(engine) as session:
        written = [
            await keys.put_key(session, "openai", long),
            await keys.put_key(session, "pagespeed", short),
        ]
        dump = (await session.execute(DUMP)).all()
        await session.rollback()
    assert [row.name for row in dump] == ["openai", "pagespeed"]
    for row, value in zip(dump, (long, short), strict=True):
        assert re.fullmatch(r"[0-9a-f]{8}", row.master_id)
        assert re.fullmatch(r"[0-9a-f]{12}", row.fingerprint)
        assert (bool(row.ciphertext), bool(row.set_at)) == (True, True)
        assert value not in " ".join(str(column) for column in row)
    assert [row.hint for row in dump] == [long[-4:], None]
    assert [item.outcome for item in written] == ["задан", "задан"]
    assert [part for part in (long, short) if part in shown(*written)] == []
    assert await count(engine) == 0


def test_a224_key_row_repr_shows_the_name_and_master_id_only() -> None:
    """A224, находка 9 проверяющей: `repr` строки ключа — имя и номер мастер-ключа, без
    шифротекста и подсказки («Безопасность», п. 2). Строка — без базы: тело `__repr__` иначе не
    исполнял ни один тест, а порч mutmut на `models.py` не строит."""
    marker = masterkeys.marker()
    row = ServiceKey(
        name="openai",
        ciphertext=marker,
        master_id="0a1b2c3d",
        fingerprint="0123456789ab",
        hint=marker[-4:],
    )
    assert repr(row) == "ServiceKey(name='openai', master_id='0a1b2c3d')"


VISIBLE = "только видимые знаки ASCII: значение уходит в заголовок запроса"
INSIDE = "без пробелов и переводов строки"
EDGES = "в начале или в конце пробел или перевод строки — уберите"
#: Отказы A225, каждый отдельно: id латиницей (L59) → имя, значение из метки и текст отказа.
REFUSED: dict[str, tuple[str, Callable[[str], str], str]] = {
    "unknown-name": ("nope", lambda m: m, "nope — такого ключа нет"),
    "empty": ("openai", lambda m: "", "OpenAI — пусто"),
    "5-signs": (
        "openai",
        lambda m: m[:5],
        "OpenAI — короче 6 знаков — маска журнала его не спрячет",
    ),
    "513-bytes": ("openai", lambda m: (m * 20)[:513], "OpenAI — длиннее 512 байт"),
    "non-ascii": ("openai", lambda m: f"{m}ключ", f"OpenAI — {VISIBLE}"),
    "nul": ("openai", lambda m: f"{m}\0x", f"OpenAI — {VISIBLE}"),
    "esc": ("openai", lambda m: f"{m}\x1bx", f"OpenAI — {VISIBLE}"),
    "del": ("openai", lambda m: f"{m}\x7fx", f"OpenAI — {VISIBLE}"),
    "space-inside": ("openai", lambda m: f"{m} x", f"OpenAI — {INSIDE}"),
    "tab-inside": ("openai", lambda m: f"{m}\tx", f"OpenAI — {INSIDE}"),
    "newline-inside": ("openai", lambda m: f"{m}\nx", f"OpenAI — {INSIDE}"),
    "space-first": ("openai", lambda m: f" {m}", f"OpenAI — {EDGES}"),
    "space-last": ("openai", lambda m: f"{m} ", f"OpenAI — {EDGES}"),
    "newline-first": ("openai", lambda m: f"\n{m}", f"OpenAI — {EDGES}"),
    "newline-last": ("openai", lambda m: f"{m}\n", f"OpenAI — {EDGES}"),
    "cr-inside": ("openai", lambda m: f"{m}\rx", f"OpenAI — {INSIDE}"),
    "cr-first": ("openai", lambda m: f"\r{m}", f"OpenAI — {EDGES}"),
    "cr-last": ("openai", lambda m: f"{m}\r", f"OpenAI — {EDGES}"),
    "vt-inside": ("openai", lambda m: f"{m}\x0bx", f"OpenAI — {VISIBLE}"),
    "ff-inside": ("openai", lambda m: f"{m}\x0cx", f"OpenAI — {VISIBLE}"),
}


@pytest.mark.parametrize("case", list(REFUSED))
async def test_a225_bad_key_is_refused_before_any_write(session: AsyncSession, case: str) -> None:
    """A225, A237: имя не из реестра, пусто, 5 знаков, 513 байт, знак не из ASCII, нулевой, `ESC`
    или `DEL`, пробел, табуляция или перевод строки внутри, пробел или перевод строки по краям —
    `KeyRefused` с именем ключа и причиной. Значения нет ни в тексте, ни в цепочке; таблица не
    тронута."""
    name, build, refusal = REFUSED[case]
    value = build(masterkeys.marker())
    with pytest.raises(keys.KeyRefused) as refused:
        await keys.put_key(session, name, value)
    assert refused.value.args == (refusal,)
    assert leaks.links(refused.value) == 0
    assert not value or value not in leaks.traces(refused.value)
    assert await count(session) == 0


@pytest.mark.parametrize(
    "value",
    ["metka!", "metka-" + "a" * 506, "!" + "~" * 5],
    ids=["6-signs", "512-bytes", "visible-edges"],
)
async def test_a225_neighbours_of_the_refusals_are_written(
    session: AsyncSession, value: str
) -> None:
    """A225, соседние случаи (L53): 6 знаков, 512 байт, крайние видимые знаки `!` и `~` — запись
    проходит."""
    assert (await keys.put_key(session, "openai", value)).outcome == "задан"


async def test_a225_unknown_name_is_refused_by_drop_and_resolve(session: AsyncSession) -> None:
    """A225, соседние случаи: имя не из реестра — «такого ключа нет» и у снятия, и у резолвера."""
    for call in (keys.drop_key, keys.resolve):
        with pytest.raises(keys.KeyRefused) as refused:
            await call(session, "nope")
        assert refused.value.args == ("nope — такого ключа нет",)


async def test_a226_same_value_keeps_the_row_and_drop_names_its_outcome(
    committed: AsyncEngine,
) -> None:
    """A226: `put_key` для `openai` со значением А дважды — второй раз строка прежняя, шифротекст и
    время те же, итог «без изменений»; со значением Б — новый отпечаток и время; `drop_key`
    дважды — «снят», затем «не был задан». Каждая запись — своей транзакцией: время записи — время
    транзакции, в одной транзакции «новое время» не проверить."""
    first, other = masterkeys.marker(), masterkeys.marker()
    outcomes, rows = [], []
    for value in (first, first, other):
        async with AsyncSession(committed) as session:
            outcomes.append((await keys.put_key(session, "openai", value)).outcome)
            await session.commit()
            rows.append((await session.execute(ROW, {"name": "openai"})).one())
    assert outcomes == ["задан", "без изменений", "задан"]
    assert rows[1] == rows[0]
    changed = [rows[2][field] != rows[0][field] for field in range(4)]
    assert changed == [True, False, True, True]
    assert rows[2].set_at > rows[0].set_at
    drops = []
    for _ in range(2):
        async with AsyncSession(committed) as session:
            drops.append(await keys.drop_key(session, "openai"))
            await session.commit()
    assert (drops, await count(committed)) == (["снят", "не был задан"], 0)


async def test_a227_resolve_sees_each_change_on_the_next_call(committed: AsyncEngine) -> None:
    """A227: две сессии на двух соединениях с фиксацией. Первая — одна на все `resolve`, без
    `expire_all` и фиксации, и держит строку ключа в своей карте сущностей, как держал бы её другой
    код той же сессии: так тест держит и кэш карты сущностей (карта ссылается слабо — без этой
    ссылки `session.get` молча шёл бы в базу), а не только свой. Не задан — `KeyMissing` «ключ
    OpenAI не задан»; запись А — А с её отпечатком; запись Б — Б со следующего вызова, без
    перезапуска и ожидания; снятие — снова `KeyMissing`."""
    first, other = masterkeys.marker(), masterkeys.marker()
    async with AsyncSession(committed) as reader, AsyncSession(committed) as writer:
        with pytest.raises(keys.KeyMissing) as missing:
            await keys.resolve(reader, "openai")
        assert missing.value.args == ("ключ OpenAI не задан",)
        seen, held = [], []
        for value in (first, other):
            written = await keys.put_key(writer, "openai", value)
            await writer.commit()
            got = await keys.resolve(reader, "openai")
            seen.append((got.value == value, got.fingerprint == written.state.fingerprint))
            held.append(await reader.get(ServiceKey, "openai"))
        assert seen == [(True, True), (True, True)]
        assert await keys.drop_key(writer, "openai") == "снят"
        await writer.commit()
        with pytest.raises(keys.KeyMissing) as gone:
            await keys.resolve(reader, "openai")
    assert leaks.links(gone.value) == 0
    assert [part for part in (first, other) if part in leaks.traces(gone.value)] == []


async def test_a231_two_writes_at_once_leave_one_row_of_the_later_commit(
    committed: AsyncEngine,
) -> None:
    """A231: два `put_key(openai)` разом на двух соединениях с барьером, значения А и Б; обе
    транзакции фиксируются — строка одна; `resolve` отдаёт значение той, что зафиксирована позже,
    отпечаток строки — от него же. Порядок фиксаций — по порядку возврата записи: вторая ждёт замка
    строки первой и возвращается только после её фиксации."""
    barrier = asyncio.Barrier(2)
    returned: list[tuple[str, keys.Written]] = []

    async def write(value: str) -> None:
        async with AsyncSession(committed) as session:
            await dbtools.meet(barrier)
            returned.append((value, await keys.put_key(session, "openai", value)))
            await session.commit()

    await asyncio.gather(write(masterkeys.marker()), write(masterkeys.marker()))
    later, written = returned[-1]
    async with AsyncSession(committed) as session:
        got = await keys.resolve(session, "openai")
    assert (await count(committed), got.value == later) == (1, True)
    assert got.fingerprint == written.state.fingerprint == crypto.seal("openai", later).fingerprint


async def test_a223_gone_previous_line_is_named_with_an_error_record(
    session: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A223, часть с базой: запись сделана, когда в файле была только вторая строка; под файлом из
    двух `resolve` её отдаёт; без второй строки — `KeyUnreadable` «ключ OpenAI зашифрован
    мастер-ключом с номером N, его нет в файле» и запись ERROR логгера `ai_geo_audit.settings`:
    `event` — `key-unreadable`, `key` — `openai`, `master_id` — номер. Значения нет ни в тексте, ни
    в записи."""
    first, second = masterkeys.line(), masterkeys.line()
    path, value = tmp_path / "master.key", masterkeys.marker()
    masterkeys.use(monkeypatch, masterkeys.write(path, [second]))
    await keys.put_key(session, "openai", value)
    master_id = (await session.execute(ROW, {"name": "openai"})).one().master_id
    masterkeys.use(monkeypatch, masterkeys.write(path, [first, second]))
    assert (await keys.resolve(session, "openai")).value == value
    masterkeys.use(monkeypatch, masterkeys.write(path, [first]))
    caplog.set_level(logging.ERROR, logger="ai_geo_audit.settings")
    with pytest.raises(keys.KeyUnreadable) as refused:
        await keys.resolve(session, "openai")
    assert refused.value.args == (
        f"ключ OpenAI зашифрован мастер-ключом с номером {master_id}, его нет в файле",
    )
    assert value not in leaks.traces(refused.value)
    [record] = [r for r in caplog.records if r.name == "ai_geo_audit.settings"]
    fields = (record.levelname, record.event, record.key, record.master_id)  # type: ignore[attr-defined]
    assert fields == ("ERROR", "key-unreadable", "openai", master_id)
    assert value not in logging.Formatter().format(record) + repr(record.__dict__)


@pytest.mark.parametrize("fmt", ["json", "text"])
async def test_a228_resolved_value_is_masked_in_the_log(
    session: AsyncSession, capsys: pytest.CaptureFixture[str], fmt: str
) -> None:
    """A228: после `resolve(openai)` со значением-меткой запись журнала с меткой в тексте и в поле
    `extra` выходит с маской `***`. Набор вычистки перед `resolve` очищен — как в процессе, где
    записи не было: метку туда кладёт сам резолвер."""
    value = masterkeys.marker()
    await keys.put_key(session, "openai", value)
    logs._secrets.clear()
    resolved = await keys.resolve(session, "openai")
    logs.setup_logging(level="INFO", fmt=fmt)
    logging.getLogger("ai_geo_audit.test").info("ключ %s", resolved.value, extra={"k": value})
    out = capsys.readouterr().err
    assert (value in out, out.count(logs.MASK) >= 2) == (False, True)
    assert value not in repr(resolved)


async def test_a230_states_follow_the_registry_without_values(
    session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A230: `states()` — `openai` задан; `pagespeed` зашифрован мастер-ключом, которого нет в
    файле; остальные не заданы. Записи — в порядке реестра; у каждой: задан ли, подсказка,
    отпечаток, время, расшифровывается ли — у `openai` да, у `pagespeed` нет. В `repr` и в JSON
    состояния нет ни метки, ни шифротекста."""
    path = tmp_path / "master.key"
    hidden, value = masterkeys.marker(12), masterkeys.marker(30)
    masterkeys.use(monkeypatch, masterkeys.write(path, [masterkeys.line()]))
    await keys.put_key(session, "pagespeed", hidden)
    masterkeys.use(monkeypatch, masterkeys.write(path, [masterkeys.line()]))
    await keys.put_key(session, "openai", value)
    found = await keys.states(session)
    ciphertexts = (await session.execute(text("SELECT ciphertext FROM service_keys"))).scalars()
    assert [state.name for state in found] == [name for name, _, _ in REGISTRY]
    assert [state.word for state in found] == [word for _, word, _ in REGISTRY]
    table = {
        s.name: (s.is_set, s.readable, s.hint, s.fingerprint is None, s.set_at is None)
        for s in found
    }
    unset = (False, None, None, True, True)
    assert table == {
        **{name: unset for name, _, _ in REGISTRY},
        "openai": (True, True, value[-4:], False, False),
        "pagespeed": (True, False, None, False, False),
    }
    out = shown(*found)
    assert [secret for secret in (hidden, value, *ciphertexts) if secret in out] == []


async def test_a230_swapped_rows_are_refused_with_the_key_word(session: AsyncSession) -> None:
    """A230, A222, «Безопасность», п. 4: строки двух ключей переставлены в базе местами — `resolve`
    отказывает «шифротекст другого ключа: OpenAI», а не отдаёт чужое значение; у обоих в
    `states()` — «не расшифровывается»: по номеру мастер-ключа они сошли бы за годные."""
    mine, theirs = masterkeys.marker(), masterkeys.marker()
    await keys.put_key(session, "openai", mine)
    await keys.put_key(session, "ahrefs", theirs)
    await session.execute(SWAP)
    with pytest.raises(keys.KeyUnreadable) as refused:
        await keys.resolve(session, "openai")
    assert refused.value.args == ("шифротекст другого ключа: OpenAI",)
    assert [part for part in (mine, theirs) if part in leaks.traces(refused.value)] == []
    readable = {state.name: state.readable for state in await keys.states(session)}
    assert (readable["openai"], readable["ahrefs"]) == (False, False)
