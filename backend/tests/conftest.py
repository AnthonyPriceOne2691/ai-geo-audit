"""Тесты не читают `.env` разработчика и не видят его переменных окружения."""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator

import pytest

# До первого импорта пакета: `config/_base.py` берёт путь к `.env` при импорте.
os.environ["ENV_FILE"] = os.devnull

from ai_geo_audit import config  # noqa: E402
from ai_geo_audit.logs import setup as log_setup  # noqa: E402


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
