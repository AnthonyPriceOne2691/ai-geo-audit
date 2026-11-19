"""Домены настроек. Имя переменной окружения стоит у поля в `validation_alias`: по нему
поле ищут в `.env.example` и в отказах проверки старта.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr, field_validator

from ai_geo_audit.config._base import Settings

#: База разработки из docker compose. В бою проверка старта требует `DATABASE_URL` явно.
_DEV_DATABASE_URL = "postgresql+asyncpg://audit:audit@localhost:5462/audit"
Level = Literal["DEBUG", "INFO", "WARNING", "ERROR"]


class AppSettings(Settings):
    """Режим. `prod` включает строгую проверку старта, `dev` работает из коробки."""

    env: Literal["dev", "test", "prod"] = Field("dev", validation_alias="APP_ENV")
    service_name: str = Field("ai-geo-audit", validation_alias="SERVICE_NAME")


class StorageSettings(Settings):
    """Postgres и Redis. В адресе базы пароль, поэтому адрес — секрет."""

    database_url: SecretStr = Field(SecretStr(_DEV_DATABASE_URL), validation_alias="DATABASE_URL")
    redis_url: str = Field("redis://localhost:6379/0", validation_alias="REDIS_URL")
    echo_sql: bool = Field(False, validation_alias="DB_ECHO_SQL")
    pool_size: int = Field(5, ge=1, le=50, validation_alias="DB_POOL_SIZE")
    max_overflow: int = Field(5, ge=0, le=50, validation_alias="DB_MAX_OVERFLOW")
    #: Пул задачи воркера: у каждой задачи свой движок, поэтому пул малый.
    task_pool_size: int = Field(2, ge=1, le=10, validation_alias="DB_TASK_POOL_SIZE")
    #: Сколько второй `migrate` ждёт замка первого. Умолчание боевое: тест укорачивает его сам.
    migration_lock_wait_seconds: int = Field(120, validation_alias="MIGRATION_LOCK_WAIT_SECONDS")

    @field_validator("migration_lock_wait_seconds")
    @classmethod
    def _lock_wait_in_bounds(cls, value: int) -> int:
        if not 1 <= value <= 3600:
            raise ValueError(f"предел ожидания замка миграций — от 1 до 3600 с (час), а не {value}")
        return value


class AccessSettings(Settings):
    """Вход: подпись пропусков и срок их жизни."""

    jwt_secret: SecretStr = Field(SecretStr(""), validation_alias="JWT_SECRET")
    jwt_ttl_hours: int = Field(12, ge=1, le=168, validation_alias="JWT_TTL_HOURS")
    jwt_algorithm: Literal["HS256"] = Field("HS256", validation_alias="JWT_ALGORITHM")


class KeySettings(Settings):
    """Путь к мастер-ключу, которым шифруются ключи внешних сервисов в базе. Файл
    монтируется только в api и воркер — не в базе и не в бэкапе.
    """

    master_key_file: str = Field("", validation_alias="KEYS_MASTER_KEY_FILE")


class LogSettings(Settings):
    """Как сервис пишет о себе: `json` — для боя, `text` — для терминала."""

    format: Literal["text", "json"] = Field("text", validation_alias="LOG_FORMAT")
    level: Level = Field("INFO", validation_alias="LOG_LEVEL")
