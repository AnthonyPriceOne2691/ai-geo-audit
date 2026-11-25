"""Тесты не ходят дальше петли, не читают `.env` разработчика и не видят его окружения.

База тестов — своя у рабочей копии (`dbtools`), база Redis — своя у процесса (`redistools`); без
Postgres или Redis прогон останавливается один раз.
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
from collections.abc import AsyncIterator, Iterator, Mapping
from pathlib import Path
from typing import NoReturn

import dbtools
import masterkeys
import mutation_db
import pytest
import redistools
from hypothesis import settings
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
from ai_geo_audit.config import master_key
from ai_geo_audit.logs import setup as log_setup
from ai_geo_audit.storage import build_engine, dispose_engine


def reproducible(environ: Mapping[str, str] = os.environ) -> dict[str, object]:
    """Профиль свойств: воспроизводимый — сид от самого теста, базы примеров нет; под гейтом
    мутаций — без срока примера (A282): в 4 потока сосед по CPU не даёт ложного «убит» по
    `DeadlineExceeded`."""
    options: dict[str, object] = {"derandomize": True, "database": None}
    if mutation_db.under_mutmut(environ):
        options["deadline"] = None
    return options


# Свойства с генератором воспроизводимы: под ними идёт мутационный гейт. Родитель — профиль по
# умолчанию, а не текущий: в CI hypothesis сам грузит свой профиль `ci`, и на машине и в CI правило
# разошлось бы.
settings.register_profile("reproducible", settings.get_profile("default"), **reproducible())
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


def refuse(message: str) -> NoReturn:
    """Сервера тестов нет — сессия не идёт: одно сообщение, под mutmut ещё и метка сбоя (A285)."""
    mutation_db.mark_refused(message)
    # Ошибка использования печатается одной строкой; pytest.exit дал бы две.
    raise pytest.UsageError(message)


def pytest_sessionstart(session: pytest.Session) -> None:
    """A12: нет Postgres или Redis — одно сообщение с командой, а не десятки пропусков. База
    сессии — база рабочей копии, под mutmut — своя копия шаблона (A281); база Redis — своя у
    процесса, под меткой со сроком."""
    reason = asyncio.run(dbtools.unreachable())
    if reason:
        refuse(f"Postgres для тестов не отвечает ({reason}). Поднять: {dbtools.START}")
    reason = redistools.session_refusal()
    if reason:
        refuse(reason)
    mutation_db.session_start()


def pytest_unconfigure(config: pytest.Config) -> None:
    """Под mutmut — снос копии сессии (A281), в самом конце: после итога A106 и развязки фикстур.
    Хук итога подключён списком импорта выше, как был (L63). Метка базы Redis снимается здесь же;
    у сессии, снятой mutmut по сроку, её отпускает срок метки (L97)."""
    mutation_db.session_end()
    redistools.session_end()


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
def forget_master_key() -> Iterator[None]:
    """Файл мастер-ключа читается один раз за процесс — после теста кэш чтения пуст."""
    yield
    master_key.read.cache_clear()


@pytest.fixture
def master_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> masterkeys.MasterFile:
    """Одна фикстура мастер-ключа на все тесты (b1:457): временный файл со случайной строкой,
    конфиг процесса смотрит в него."""
    lines = [masterkeys.line()]
    path = masterkeys.write(tmp_path / "master.key", lines)
    masterkeys.use(monkeypatch, path)
    return masterkeys.MasterFile(path, lines)


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


@pytest.fixture
def redis_url() -> Iterator[str]:
    """Адрес Redis теста — база сессии, пустая до и после теста: ключи в коде прежние (L97)."""
    db = redistools.session_db()
    redistools.flush(db)
    yield redistools.url(db)
    redistools.flush(db)
