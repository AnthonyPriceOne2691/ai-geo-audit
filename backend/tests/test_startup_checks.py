"""Проверка старта: в бою называет все негодные настройки разом, в разработке молчит."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_geo_audit import config
from ai_geo_audit.config import startup_checks as sc

DSN = "postgresql+asyncpg://audit:pw-1234567890@db:5432/audit"


@pytest.fixture
def master_key(tmp_path: Path) -> str:
    path = tmp_path / "master.key"
    path.write_text("k" * 44, encoding="utf-8")
    return str(path)


def full_env(master_key: str) -> dict[str, str]:
    return {
        "DATABASE_URL": DSN,
        "REDIS_URL": "redis://redis:6379/0",
        "JWT_SECRET": "a" * 64,
        "KEYS_MASTER_KEY_FILE": master_key,
    }


def prod(monkeypatch: pytest.MonkeyPatch, process: sc.Process, **env: str) -> list[sc.Problem]:
    """Негодные настройки процесса в боевом режиме при этом окружении."""
    monkeypatch.setenv("APP_ENV", "prod")
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return sc.problems(
        process,
        app=config.AppSettings(),
        storage=config.StorageSettings(),
        access=config.AccessSettings(),
        keys=config.KeySettings(),
    )


def names(found: list[sc.Problem]) -> set[str]:
    return {p.env_name for p in found}


def test_a3_prod_names_every_missing_variable_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """A3: пустой боевой режим — отказ, и в тексте все четыре имени, а не первое."""
    with pytest.raises(sc.ConfigError) as refusal:
        sc.check_startup("api", prod(monkeypatch, "api"))
    for name in ("DATABASE_URL", "REDIS_URL", "JWT_SECRET", "KEYS_MASTER_KEY_FILE"):
        assert name in str(refusal.value)


def test_a3_placeholders_are_refused(monkeypatch: pytest.MonkeyPatch, master_key: str) -> None:
    """A3: заглушка `.env.example` в бою не принимается — даже длинная, даже как путь."""
    env = full_env(master_key) | {
        "JWT_SECRET": "CHANGE_ME_" + "x" * 40,
        "DATABASE_URL": "postgresql+asyncpg://audit:CHANGE_ME@db:5432/audit",
        "KEYS_MASTER_KEY_FILE": "CHANGE_ME",
    }
    found = prod(monkeypatch, "api", **env)
    assert names(found) == {"JWT_SECRET", "DATABASE_URL", "KEYS_MASTER_KEY_FILE"}
    assert all("заглушка" in p.why for p in found), found


def test_a3_short_secret_is_refused(monkeypatch: pytest.MonkeyPatch, master_key: str) -> None:
    """A3: секрет подписи короче 32 байт не годится."""
    found = prod(monkeypatch, "api", **full_env(master_key) | {"JWT_SECRET": "short-secret"})
    assert [(p.env_name, "короче 32 байт" in p.why) for p in found] == [("JWT_SECRET", True)]


def test_a3_master_key_file_must_exist(monkeypatch: pytest.MonkeyPatch, master_key: str) -> None:
    """A3: путь к мастер-ключу задан, а файла нет — отказ с этим путём."""
    env = full_env(master_key) | {"KEYS_MASTER_KEY_FILE": "/nonexistent/master.key"}
    assert prod(monkeypatch, "api", **env) == [
        sc.Problem("KEYS_MASTER_KEY_FILE", "файла нет: /nonexistent/master.key")
    ]


def test_a3_processes_need_different_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """A3: миграциям хватает базы; воркеру не нужен секрет входа, api — нужно всё."""
    assert names(prod(monkeypatch, "migrate", DATABASE_URL=DSN)) == set()
    worker = {"REDIS_URL", "KEYS_MASTER_KEY_FILE"}
    assert names(prod(monkeypatch, "worker", DATABASE_URL=DSN)) == worker
    assert names(prod(monkeypatch, "api", DATABASE_URL=DSN)) == worker | {"JWT_SECRET"}


def test_a4_starts_without_external_keys(monkeypatch: pytest.MonkeyPatch, master_key: str) -> None:
    """A4: ключи OpenAI, DataForSEO и Ahrefs для старта не нужны и конфигом не читаются."""
    assert prod(monkeypatch, "api", **full_env(master_key)) == []
    providers = ("OPENAI", "DATAFORSEO", "AHREFS")
    assert not [name for name in config.env_names() if any(p in name for p in providers)]


@pytest.mark.parametrize("env", ["dev", "test"])
def test_outside_prod_checks_are_silent(monkeypatch: pytest.MonkeyPatch, env: str) -> None:
    """Разработка и тесты стартуют без единой настройки."""
    monkeypatch.setenv("APP_ENV", env)
    assert sc.problems("api", app=config.AppSettings()) == []
