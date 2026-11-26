"""Redis тестов: у процесса pytest своя база из 1–15 на сессию, ключи в коде прежние (L97).

Redis стенда один на машину, как Postgres 5462: его делят рабочие копии и потоки порч. Базу
занимает метка в базе 0 — `SET NX` со сроком; поток сессии продлевает её чаще срока, конец сессии
снимает. Сессию, снятую по сроку (порча mutmut, `os._exit`, убийство), отпускает срок метки. Адрес
сервера — только `TEST_REDIS_URL`, без номера базы; `REDIS_URL` тесты не читают. Пароль —
публичный пароль стенда, как `audit` у Postgres: в бою адрес Redis задаётся явно.
"""

from __future__ import annotations

import contextlib
import os
import threading
from collections.abc import Callable
from urllib.parse import urlsplit, urlunsplit

import envguard
import redis
from redis.backoff import NoBackoff
from redis.client import Pipeline
from redis.retry import Retry

PASSWORD = "audit-dev-redis"  # pragma: allowlist secret
SERVER = f"redis://:{PASSWORD}@127.0.0.1:6462"
START = "docker compose -f docker-compose.dev.yml up -d redis"
#: Базы сессий; в базе 0 — только метки занятых.
DATABASES = range(1, 16)
LABEL = "ai-geo-audit:test:db:"
#: Срок метки и шаг продления: снятая сессия отпускает базу не позже срока (L97).
TTL_MS = 60_000
RENEW_SECONDS = 15.0
#: Срок ответа Redis оснастке: молчащий называется причиной, а не держит сессию.
TIMEOUT = 3


class NoFreeDatabaseError(RuntimeError):
    """Свободной базы Redis для тестов нет."""


def label(db: int) -> str:
    return f"{LABEL}{db}"


def owner() -> str:
    """Кто держит базу: корень копии и pid — по метке видно, чья она."""
    return f"{envguard.ROOT}|{os.getpid()}"


def url(db: int) -> str:
    """Адрес базы `db` на сервере тестов."""
    parts = urlsplit(os.environ.get("TEST_REDIS_URL", SERVER))
    return urlunsplit(parts._replace(path=f"/{db}"))


#: Клиенты оснастки по адресу — один на адрес за сессию.
_CLIENTS: dict[str, redis.Redis] = {}


def client(db: int = 0) -> redis.Redis:
    """Клиент оснастки: без повторов, со сроком — как у кода под тестом (decisions, срез 0). Один
    на адрес за сессию, закрывает его `session_end()`, а не сборщик мусора: незакрытый сокет,
    собранный в `pytest_unconfigure`, ронял долю порч (CI 45626254595, срез 3)."""
    address = url(db)
    if address not in _CLIENTS:
        _CLIENTS[address] = redis.Redis.from_url(
            address,
            socket_timeout=TIMEOUT,
            socket_connect_timeout=TIMEOUT,
            retry=Retry(NoBackoff(), 0),
        )
    return _CLIENTS[address]


def close_clients() -> None:
    """Закрыть соединения клиентов оснастки: после сессии сокетов к Redis теста не остаётся."""
    while _CLIENTS:
        _CLIENTS.popitem()[1].close()


def unreachable() -> str | None:
    """Почему Redis тестов не отвечает, или None."""
    try:
        client().ping()
    except redis.RedisError as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


def flush(db: int) -> None:
    """Очистить базу сессии: метки живут в базе 0 и не задеты."""
    client(db).flushdb()


def claim(who: str | None = None, ttl_ms: int = TTL_MS) -> int:
    """Занять свободную базу меткой со сроком; свободной нет — `NoFreeDatabaseError`. Базу чистит
    фикстура `redis_url` до и после теста, поэтому здесь не чистим."""
    admin, me = client(), who or owner()
    for db in DATABASES:
        if admin.set(label(db), me, nx=True, px=ttl_ms):
            return db
    holders = [value.decode() for value in admin.mget([label(db) for db in DATABASES]) if value]
    raise NoFreeDatabaseError(
        f"свободной базы Redis для тестов нет: заняты базы {DATABASES[0]}–{DATABASES[-1]} "
        f"(метки {LABEL}N в базе 0); снятые сессии отпустит срок метки — "
        f"{ttl_ms // 1000} с. Держат: {'; '.join(holders)}"
    )


def _if_own(key: str, me: str, act: Callable[[Pipeline], object]) -> bool:
    """`act` над меткой — только если метка своя: `WATCH`, чтобы не снять и не продлить чужую."""
    with client().pipeline() as pipe:
        try:
            pipe.watch(key)
            if pipe.get(key) != me.encode():
                return False
            pipe.multi()
            act(pipe)
            pipe.execute()
        except redis.WatchError:
            return False
    return True


def release(db: int, who: str | None = None) -> bool:
    """Снять свою метку базы `db`; чужую не трогать."""
    key = label(db)
    return _if_own(key, who or owner(), lambda pipe: pipe.delete(key))


class Holder:
    """Продление метки базы из потока сессии, пока сессия жива."""

    def __init__(self, db: int, who: str, ttl_ms: int = TTL_MS, every: float = RENEW_SECONDS):
        self.db, self.who, self.ttl_ms, self.every = db, who, ttl_ms, every
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"redis-db-{db}", daemon=True)

    def _run(self) -> None:
        key = label(self.db)
        while not self._stop.wait(self.every):
            with contextlib.suppress(redis.RedisError):
                _if_own(key, self.who, lambda pipe: pipe.pexpire(key, self.ttl_ms))

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=TIMEOUT + self.every)


#: Метки сессий этого процесса; действующая — последняя.
SESSION: list[Holder] = []


def session_start() -> int:
    """Занять базу сессии и продлевать её метку до `session_end()`."""
    holder = Holder(claim(), owner())
    holder.start()
    SESSION.append(holder)
    return holder.db


def session_refusal() -> str | None:
    """Занять базу сессии; почему не вышло — одной строкой, или None."""
    try:
        session_start()
    except NoFreeDatabaseError as exc:
        return str(exc)
    except redis.RedisError as exc:
        return f"Redis для тестов не отвечает ({type(exc).__name__}: {exc}). Поднять: {START}"
    return None


def session_end() -> None:
    """Остановить продление, снять метки сессий этого процесса и закрыть клиентов оснастки."""
    while SESSION:
        holder = SESSION.pop()
        holder.stop()
        with contextlib.suppress(redis.RedisError):
            release(holder.db, holder.who)
    close_clients()


def current_url() -> str:
    """Адрес Redis для кода под тестом и для сервера сценариев: база сессии pytest; вне сессии
    (smoke `run.sh`) — база 0, где живут только метки и ключ пробы здоровья."""
    return url(SESSION[-1].db if SESSION else 0)


def session_db() -> int:
    if not SESSION:
        raise RuntimeError("база Redis сессии не занята: conftest не звал session_start()")
    return SESSION[-1].db
