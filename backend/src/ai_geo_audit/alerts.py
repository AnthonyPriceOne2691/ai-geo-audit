"""Тревоги: текст и постановка в исходящие Redis; доставку делает хост (Ч1.14а2).

Правило для авторов тревог (В7, PHASES:69): в заголовке, строках и совете нет данных клиента и
имени класса исключения — вид сбоя пишется словами. Строки проходят вычистку журнала: секрет
настроек внутри строки становится маской — это страховка, а не разрешение.

Текст — простой, без разметки::

    [ai-geo-audit] <УРОВЕНЬ>: <заголовок>      вне боя — [ai-geo-audit · <APP_ENV>]
    Время: <UTC до секунды, на Z>
    <строки тела>
    Что делать: <совет>

Длина — до 4 096 единиц UTF-16: так считает Telegram. Заголовок длиннее 200, совет длиннее 500 и
строка тела длиннее 1 000 единиц обрезаются с «…»; дальше строки тела режутся с конца, на их месте
— «…ещё N строк». Первая и последняя строки целы.

Запись исходящих — договор с Ч1.14а2: JSON только из ASCII, одной строкой, ровно с ключами `id` (32
знака hex из `uuid4`), `ts` и `text`. Постановка — `WATCH` и `MULTI`: запись в начало списка,
список не длиннее 1 000, отброшенное с хвоста — в счётчик для хоста и строкой журнала. Срок всей
постановки — 2 с; повторов у клиента нет: повтор `EXEC` после потерянного ответа положил бы
тревогу дважды (decisions, срез 0).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Literal

import redis.asyncio as aredis
from redis import exceptions
from redis.backoff import NoBackoff
from redis.retry import Retry

from ai_geo_audit import config
from ai_geo_audit.logs import redact, register_config_secrets

Level = Literal["СБОЙ", "ВНИМАНИЕ", "ВОССТАНОВЛЕНО", "ПРОВЕРКА"]
SERVICE = "ai-geo-audit"
OUTBOX = "ai-geo-audit:alerts:out"
DROPPED = "ai-geo-audit:alerts:dropped"
#: Пределы — защита, а не настройка (решение владельца 2026-11-19T13:13Z).
MAX_ALERTS = 1000
MAX_UNITS = 4096
TITLE_UNITS, TODO_UNITS, LINE_UNITS = 200, 500, 1000
DEADLINE_SECONDS = 2.0
#: Закрытие соединения после постановки — не дольше: вежливое закрытие не растягивает вызов.
CLOSE_SECONDS = 0.5
ELLIPSIS = "…"
_log = logging.getLogger(__name__)


class InLoopError(RuntimeError):
    """Синхронную постановку позвали из работающего цикла событий."""


def units(text: str) -> int:
    """Длина в единицах UTF-16 — так считает Telegram: знак вне основной плоскости — две."""
    return len(text.encode("utf-16-le")) // 2


def cut(text: str, limit: int) -> str:
    """Текст не длиннее `limit` единиц: длиннее — обрезан по знакам с «…» в конце."""
    if units(text) <= limit:
        return text
    kept: list[str] = []
    used = 0
    for char in text:
        used += units(char)
        if used > limit - 1:
            break
        kept.append(char)
    return "".join(kept) + ELLIPSIS


def more_lines(count: int) -> str:
    """«…ещё N строк» с формой слова по числу: 1 строка, 2 строки, 5 строк."""
    if count % 10 == 1 and count % 100 != 11:
        word = "строка"
    elif 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        word = "строки"
    else:
        word = "строк"
    return f"{ELLIPSIS}ещё {count} {word}"


def stamp(moment: datetime) -> str:
    """Время тревоги — UTC до секунды, на `Z`."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def compose(level: Level, title: str, lines: Sequence[str], todo: str, now: datetime) -> str:
    """Текст тревоги по правилам модуля; секреты настроек в частях — маской вычистки журнала."""
    register_config_secrets(config.storage, config.access, config.keys)
    env = config.app.env
    name = SERVICE if env == "prod" else f"{SERVICE} · {env}"
    head = [f"[{name}] {level}: {cut(redact(title), TITLE_UNITS)}", f"Время: {stamp(now)}"]
    tail = f"Что делать: {cut(redact(todo), TODO_UNITS)}"
    kept = [cut(redact(line), LINE_UNITS) for line in lines]
    text, dropped = "\n".join([*head, *kept, tail]), 0
    while units(text) > MAX_UNITS and kept:
        kept.pop()
        dropped += 1
        text = "\n".join([*head, *kept, more_lines(dropped), tail])
    return text


#: Вид сбоя по классу исключения — сверху вниз, подкласс раньше родителя: отказ входа у клиента
#: `redis` — подкласс отказа соединения.
KINDS: tuple[tuple[type[BaseException] | tuple[type[BaseException], ...], str], ...] = (
    (InLoopError, "вызов из работающего цикла событий: нужен асинхронный вариант (Ч1.14б)"),
    ((TimeoutError, exceptions.TimeoutError), f"Redis не ответил за {DEADLINE_SECONDS:g} с"),
    (exceptions.AuthenticationError, "Redis не пустил: нужен верный пароль в адресе"),
    (exceptions.OutOfMemoryError, "Redis полон: запись отвергнута"),
    (exceptions.ConnectionError, "Redis не принимает соединение"),
    (exceptions.RedisError, "Redis ответил ошибкой"),
)


def kind(failure: BaseException) -> str:
    """Вид сбоя словами — без текста исключения: в нём бывают адрес и сама запись."""
    return next((words for found, words in KINDS if isinstance(failure, found)), "сбой постановки")


async def _push(client: aredis.Redis, record: str) -> int:
    """Запись в начало, список не длиннее `MAX_ALERTS`, отброшенное — в счётчик; число
    отброшенных. Чужая правка списка между `LLEN` и `EXEC` — `WatchError`: транзакция не
    применилась, круг заново."""
    while True:
        async with client.pipeline(transaction=True) as pipe:
            try:
                await pipe.watch(OUTBOX)
                over = max(0, await pipe.llen(OUTBOX) + 1 - MAX_ALERTS)
                pipe.multi()  # type: ignore[no-untyped-call]
                pipe.lpush(OUTBOX, record)
                pipe.ltrim(OUTBOX, 0, MAX_ALERTS - 1)
                if over:
                    pipe.incrby(DROPPED, over)
                await pipe.execute()
            except exceptions.WatchError:
                continue
            return over


async def _queue(record: str) -> int:
    """Постановка под общим сроком; соединение — своё, закрытие ограничено отдельно."""
    url = config.storage.redis_url.get_secret_value()
    client = aredis.Redis.from_url(url, retry=Retry(NoBackoff(), 0))
    try:
        async with asyncio.timeout(DEADLINE_SECONDS):
            return await _push(client, record)
    finally:
        try:
            async with asyncio.timeout(CLOSE_SECONDS):
                await client.aclose()
        except (TimeoutError, exceptions.RedisError, OSError):
            _log.debug("соединение Redis исходящих закрыто без ожидания")


def _in_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def queue_alert(level: Level, title: str, lines: Sequence[str], todo: str) -> bool:
    """Поставить тревогу в исходящие. `True` — запись в списке; `False` — нет, и строка ERROR
    «ТРЕВОГА НЕ ПОСТАВЛЕНА» с видом сбоя словами. Исключений вызывающий не видит (3-resume:22).
    Вызов синхронный: из работающего цикла событий — `False` (асинхронный вариант — Ч1.14б)."""
    try:
        if _in_loop():
            raise InLoopError
        now = datetime.now(UTC)
        text = compose(level, title, lines, todo, now)
        record = json.dumps({"id": uuid.uuid4().hex, "ts": stamp(now), "text": text})
        dropped = asyncio.run(_queue(record))
    except Exception as failure:  # вызывающий исключений не видит: итог и строка ERROR
        _log.error("ТРЕВОГА НЕ ПОСТАВЛЕНА — %s: %s «%s»", kind(failure), level, title)
        return False
    if dropped:
        _log.warning("исходящие полны — отброшено %d", dropped)
    return True
