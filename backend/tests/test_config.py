"""Конфиг: работает из коробки, читает переменные по имени, `.env.example` полон."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import SecretStr

from ai_geo_audit import config
from ai_geo_audit.config.startup_checks import PLACEHOLDER

ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"


def test_a1_dev_works_out_of_the_box() -> None:
    """A1: без `.env` и переменных — рабочий набор разработчика."""
    storage = config.StorageSettings()
    assert config.AppSettings().env == "dev"
    assert storage.database_url.get_secret_value().startswith("postgresql+asyncpg://")
    assert storage.redis_url == "redis://localhost:6379/0"
    assert config.AccessSettings().jwt_ttl_hours == 12
    assert config.LogSettings().format == "text"


#: Переменная, значение в окружении, домен, поле, что ожидаем в поле.
CASES = [
    ("APP_ENV", "prod", config.AppSettings, "env", "prod"),
    ("SERVICE_NAME", "audit-x", config.AppSettings, "service_name", "audit-x"),
    ("DATABASE_URL", "postgresql+asyncpg://u:p@db/x", config.StorageSettings, "database_url", None),
    ("REDIS_URL", "redis://redis:6379/1", config.StorageSettings, "redis_url", None),
    ("DB_ECHO_SQL", "true", config.StorageSettings, "echo_sql", True),
    ("DB_POOL_SIZE", "7", config.StorageSettings, "pool_size", 7),
    ("DB_MAX_OVERFLOW", "0", config.StorageSettings, "max_overflow", 0),
    ("DB_TASK_POOL_SIZE", "3", config.StorageSettings, "task_pool_size", 3),
    ("JWT_SECRET", "s" * 40, config.AccessSettings, "jwt_secret", None),
    ("JWT_TTL_HOURS", "24", config.AccessSettings, "jwt_ttl_hours", 24),
    ("JWT_ALGORITHM", "HS256", config.AccessSettings, "jwt_algorithm", "HS256"),
    ("KEYS_MASTER_KEY_FILE", "/run/key", config.KeySettings, "master_key_file", None),
    ("LOG_FORMAT", "json", config.LogSettings, "format", "json"),
    ("LOG_LEVEL", "WARNING", config.LogSettings, "level", "WARNING"),
]


@pytest.mark.parametrize(("env_name", "raw", "domain", "field", "expected"), CASES)
def test_a2_each_setting_reads_its_env_name(
    monkeypatch: pytest.MonkeyPatch,
    env_name: str,
    raw: str,
    domain: type[config.Settings],
    field: str,
    expected: object,
) -> None:
    """A2: каждую настройку задаёт переменная окружения с её именем (None — то же значение)."""
    monkeypatch.setenv(env_name, raw)
    value = getattr(domain(), field)
    value = value.get_secret_value() if isinstance(value, SecretStr) else value
    assert value == (raw if expected is None else expected)


def test_a2_table_covers_every_setting() -> None:
    """A2: новая настройка без строки в таблице выше роняет этот тест."""
    assert sorted(case[0] for case in CASES) == config.env_names()


def test_a2_constructor_accepts_field_name() -> None:
    """A2: имя поля в конструкторе работает, а не молча теряется."""
    assert config.AccessSettings(jwt_ttl_hours=6).jwt_ttl_hours == 6


def test_a2_bad_value_is_refused_by_type(monkeypatch: pytest.MonkeyPatch) -> None:
    """Значение вне допустимых — ошибка при чтении, а не тихий дефолт."""
    monkeypatch.setenv("LOG_FORMAT", "xml")
    with pytest.raises(ValueError, match="LOG_FORMAT"):
        config.LogSettings()


def _example() -> dict[str, str]:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    return dict(re.findall(r"(?m)^([A-Z][A-Z0-9_]*)=(.*)$", text))


def test_a8_env_example_lists_every_setting_and_nothing_else() -> None:
    """A8: в `.env.example` ровно те переменные, что читает конфиг."""
    assert sorted(_example()) == config.env_names()


def test_a8_secrets_in_env_example_are_placeholders() -> None:
    """A8: секреты и путь к мастер-ключу в примере — заглушки, которые бой отвергает."""
    secrets = [
        str(f.validation_alias)
        for d in config.DOMAINS
        for f in d.model_fields.values()
        if f.annotation is SecretStr
    ]
    assert secrets, "секретных настроек нет — тест ничего не проверил бы"
    example = _example()
    for name in [*secrets, "KEYS_MASTER_KEY_FILE"]:
        assert PLACEHOLDER in example[name], name


COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.dev.yml"


def test_a13_dev_database_is_the_compose_one_on_5462() -> None:
    """A13: база разработки по умолчанию — из docker-compose.dev.yml на 5462, стек назван явно."""
    compose = COMPOSE.read_text(encoding="utf-8")
    assert "@localhost:5462/" in config.StorageSettings().database_url.get_secret_value()
    assert '"127.0.0.1:5462:5432"' in compose
    assert re.search(r"(?m)^name: ai-geo-audit-dev$", compose)
