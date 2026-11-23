"""Тесты не ходят дальше петли, не читают `.env` разработчика и не видят его окружения.

База тестов — своя у рабочей копии (`dbtools`); без Postgres прогон останавливается один раз.
"""

from __future__ import annotations

# Запрет сети — до любого импорта сервиса и до фикстур уровня сессии (A30); вычистка окружения —
# до первого импорта пакета: конфиг читает окружение при импорте (A31).
import netguard  # isort: skip

netguard.install()
import envguard  # isort: skip

CLEANED = envguard.clean()

import asyncio
import logging
import os
import tempfile
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import dbtools
import pytest
from hypothesis import settings
from mutation_timing import (  # noqa: F401 -- проба хода 0 «Скорости CI», только в ветке-пробе
    mark,
    pytest_configure,
    pytest_runtestloop,
    pytest_unconfigure,
)
from run_summary import (  # noqa: F401 -- хуки итога A106 подключаются по именам
    pytest_collection,
    pytest_collection_finish,
    pytest_collectreport,
    pytest_runtest_logreport,
    pytest_sessionfinish,
    pytest_terminal_summary,
)
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from ai_geo_audit import config
from ai_geo_audit.logs import setup as log_setup
from ai_geo_audit.storage import build_engine, dispose_engine

# Свойства с генератором воспроизводимы: под ними идёт мутационный гейт. Сид — от самого теста,
# базы примеров нет. Родитель — профиль по умолчанию, а не текущий: в CI hypothesis сам грузит свой
# профиль `ci`, и на машине и в CI правило разошлось бы.
settings.register_profile(
    "reproducible", settings.get_profile("default"), derandomize=True, database=None
)
settings.load_profile("reproducible")
# Хранилище hypothesis — вне дерева: кэш констант исходников (его собирает ещё сбор тестов), таблицу
# юникода и заплатки упавших примеров он пишет и без базы примеров, по умолчанию — в `.hypothesis/`
# текущего каталога.
os.environ.setdefault(
    "HYPOTHESIS_STORAGE_DIRECTORY", str(Path(tempfile.gettempdir()) / "hypothesis-ai-geo-audit")
)


def pytest_report_header() -> str:
    """В шапке прогона — число вычищенных имён окружения, без значений (A31)."""
    return f"окружение тестов: вычищено имён — {len(CLEANED)}; сеть — только петля"


def pytest_sessionstart(session: pytest.Session) -> None:
    """A12: нет Postgres — одно сообщение с командой, а не десятки пропусков."""
    mark("start")
    reason = asyncio.run(dbtools.unreachable())
    if reason:
        # Ошибка использования печатается одной строкой; pytest.exit дал бы две.
        message = f"Postgres для тестов не отвечает ({reason}). Поднять: {dbtools.START}"
        raise pytest.UsageError(message)
    asyncio.run(dbtools.prepare())
    mark("prepared")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Переменные конфига из окружения разработчика тестам не видны."""
    for name in config.env_names():
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    """Корневой логгер и список секретов после теста — как до него."""
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    secrets = set(log_setup._secrets)
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
    log_setup._secrets.clear()
    log_setup._secrets.update(secrets)


@pytest.fixture(autouse=True)
async def reset_process_engine() -> AsyncIterator[None]:
    """Движок процесса — состояние модуля: после каждого теста он закрыт и забыт."""
    yield
    await dispose_engine()


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    """Движок тестовой базы без пула: соединение на сессию, утечка видна сразу."""
    engine = build_engine("test", url=dbtools.own_url())
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Сессия во внешней транзакции теста: после теста от неё ничего не остаётся."""
    async with dbtools.outer_session(engine) as session:
        yield session


@pytest.fixture
async def committed(engine: AsyncEngine) -> AsyncIterator[AsyncEngine]:
    """Настоящие фиксации на независимых соединениях; до и после теста проба пуста."""
    await dbtools.require_empty(engine)
    yield engine
    await dbtools.truncate(engine)
