"""Конфиг: работает из коробки, читает переменные по имени, `.env.example` полон."""

from __future__ import annotations

import re
import typing
from collections.abc import Iterable
from urllib.parse import urlsplit

import dbtools
import pytest
from pydantic import Field, SecretStr

from ai_geo_audit import config
from ai_geo_audit.config import startup_checks as sc
from ai_geo_audit.config.startup_checks import PLACEHOLDER

ENV_EXAMPLE = dbtools.ROOT / ".env.example"


def test_a1_dev_works_out_of_the_box() -> None:
    """A1: без `.env` и переменных — рабочий набор разработчика; адрес Redis — секрет (A21)."""
    storage = config.StorageSettings()
    assert config.AppSettings().env == "dev"
    assert storage.database_url.get_secret_value().startswith("postgresql+asyncpg://")
    assert storage.redis_url.get_secret_value() == "redis://localhost:6379/0"
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
    (
        "MIGRATION_LOCK_WAIT_SECONDS",
        "300",
        config.StorageSettings,
        "migration_lock_wait_seconds",
        300,
    ),
    ("JWT_SECRET", "s" * 40, config.AccessSettings, "jwt_secret", None),
    ("JWT_TTL_HOURS", "24", config.AccessSettings, "jwt_ttl_hours", 24),
    ("KEYS_MASTER_KEY_FILE", "/run/key", config.KeySettings, "master_key_file", None),
    ("LOG_FORMAT", "json", config.LogSettings, "format", "json"),
    ("LOG_LEVEL", "WARNING", config.LogSettings, "level", "WARNING"),
    ("API_HOST", "0.0.0.0", config.ApiSettings, "host", "0.0.0.0"),
    ("API_PORT", "8200", config.ApiSettings, "port", 8200),
    ("API_TRUSTED_PROXIES", "10.9.9.9", config.ApiSettings, "trusted_proxies_text", "10.9.9.9"),
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


#: Имя переменной, по которому настройка — секрет, как бы её ни объявили.
SECRET_NAME = re.compile(r"_(KEY|SECRET|PASSWORD|TOKEN|LOGIN|DSN)$")


def _is_secret(annotation: object) -> bool:
    """`SecretStr` и `SecretStr | None`: необязательный секрет — тоже секрет."""
    return annotation is SecretStr or SecretStr in typing.get_args(annotation)


def _placeholder(value: str) -> bool:
    """Заглушка — метка `CHANGE_ME`; у секрета-адреса — сам пароль внутри адреса."""
    if "://" in value:
        return urlsplit(value).password == PLACEHOLDER
    return value.startswith(PLACEHOLDER)


def sample_problems(
    example: dict[str, str], domains: Iterable[type[config.Settings]]
) -> tuple[list[str], int]:
    """Что не так с секретами образца, и сколько переменных просмотрено."""
    problems: list[str] = []
    checked = 0
    for domain in domains:
        for field in domain.model_fields.values():
            name, checked = str(field.validation_alias), checked + 1
            secret = _is_secret(field.annotation)
            if SECRET_NAME.search(name) and not secret:
                problems.append(f"{name}: имя секрета, а тип не SecretStr — его напечатает repr")
            if secret and not _placeholder(example.get(name, "")):
                problems.append(f"{name}: в образце не заглушка {PLACEHOLDER}")
    return problems, checked


def test_a8_secrets_in_env_example_are_placeholders() -> None:
    """A8: секреты и путь к мастер-ключу в образце — заглушки, которые бой отвергает; у адресов
    базы и Redis заглушка стоит на месте пароля. Число просмотренных переменных — в выводе."""
    problems, checked = sample_problems(_example(), config.DOMAINS)
    print(f"A8: просмотрено переменных: {checked}")
    assert checked == len(config.env_names())
    assert problems == []
    assert PLACEHOLDER in _example()["KEYS_MASTER_KEY_FILE"]


class _Probe(config.Settings):
    """Подставные настройки: необязательный секрет, секрет-адрес и имя секрета без типа."""

    token: SecretStr | None = Field(None, validation_alias="PROBE_TOKEN")
    url: SecretStr = Field(SecretStr(""), validation_alias="PROBE_URL")
    api_token: str = Field("", validation_alias="PROBE_API_TOKEN")


def test_a8_optional_secret_and_real_password_in_a_secret_url_are_caught() -> None:
    """A8: у необязательного секрета в образце не заглушка; у секрета-адреса настоящий пароль, а
    метка `CHANGE_ME` стоит в другом месте адреса — оба случая красные."""
    example = {
        "PROBE_TOKEN": "tok-looks-real-123",
        "PROBE_URL": "redis://:real-pass-123456@localhost:6379/CHANGE_ME",
        "PROBE_API_TOKEN": PLACEHOLDER,
    }
    problems, checked = sample_problems(example, [_Probe])
    assert checked == 3
    assert "PROBE_TOKEN: в образце не заглушка CHANGE_ME" in problems
    assert "PROBE_URL: в образце не заглушка CHANGE_ME" in problems


def test_a8_setting_named_as_a_secret_must_be_secret_str() -> None:
    """A8: настройка с именем секрета (`…_TOKEN`) объявлена не `SecretStr` — красный сам по себе,
    даже с заглушкой в образце: такую настройку печатает `repr`."""
    problems, _ = sample_problems({"PROBE_API_TOKEN": PLACEHOLDER}, [_Probe])
    assert "PROBE_API_TOKEN: имя секрета, а тип не SecretStr — его напечатает repr" in problems


#: Блок переменных сервера в образце (A176): строки `# ИМЯ=значение` читает compose, а не
#: процессы, поэтому A8 их не видит. Пароли — только заглушкой.
SERVER_PASSWORDS = (
    "DB_PASSWORD",
    "POSTGRES_MIGRATE_PASSWORD",
    "POSTGRES_APP_PASSWORD",
    "REDIS_PASSWORD",
)
#: Адрес сервера → процесс, чья роль базы стоит в адресе; у адреса очереди роли нет.
SERVER_URLS: dict[str, sc.Process | None] = {
    "MIGRATE_DATABASE_URL": "migrate",
    "API_DATABASE_URL": "api",
    "API_REDIS_URL": None,
}
GOOD_BLOCK = dict.fromkeys(SERVER_PASSWORDS, PLACEHOLDER) | {
    # pragma: allowlist nextline secret
    "MIGRATE_DATABASE_URL": "postgresql+asyncpg://audit_migrate:CHANGE_ME@db:5432/audit",
    # pragma: allowlist nextline secret
    "API_DATABASE_URL": "postgresql+asyncpg://audit_app:CHANGE_ME@db:5432/audit",
    "API_REDIS_URL": "redis://:CHANGE_ME@redis:6379/0",
}


def _server_block() -> dict[str, str]:
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    return dict(re.findall(r"(?m)^# ([A-Z][A-Z0-9_]*)=(.*)$", text))


def server_problems(block: dict[str, str]) -> tuple[list[str], int]:
    """Что не так с блоком сервера — имя переменной и что не так, без значения; и сколько
    переменных просмотрено."""
    problems = [
        f"{name}: в образце не заглушка {PLACEHOLDER}"
        for name in SERVER_PASSWORDS
        if block.get(name) != PLACEHOLDER
    ]
    for name, process in SERVER_URLS.items():
        parts = urlsplit(block.get(name, ""))
        if parts.password != PLACEHOLDER:
            problems.append(f"{name}: на месте пароля не заглушка {PLACEHOLDER}")
        if process is not None and parts.username != sc.DB_ROLES[process]:
            problems.append(f"{name}: в адресе не роль {sc.DB_ROLES[process]}")
    return problems, len(SERVER_PASSWORDS) + len(SERVER_URLS)


def test_a176_server_block_has_placeholders_and_process_roles() -> None:
    """A176: блок переменных сервера в `.env.example` — заглушка `CHANGE_ME` у четырёх паролей
    сервера (база, роль миграций, роль сервиса, очередь) и на месте пароля у адресов
    `MIGRATE_DATABASE_URL`, `API_DATABASE_URL` и `API_REDIS_URL`; имя роли в адресах базы — из
    таблицы ролей `startup_checks`. Число просмотренных — в выводе."""
    problems, checked = server_problems(_server_block())
    print(f"A176: просмотрено переменных сервера: {checked}")
    assert checked == len(SERVER_PASSWORDS) + len(SERVER_URLS) == 7
    assert problems == []


def test_a176_real_password_and_migrate_role_in_api_url_are_caught() -> None:
    """A176: порчи «настоящий пароль в образце» и «в адресе api — роль миграций» — красное с
    именем переменной, без значения; годный блок — без замечаний (держится до кода)."""
    assert server_problems(GOOD_BLOCK) == ([], 7)
    spoiled = GOOD_BLOCK | {
        "POSTGRES_APP_PASSWORD": "looks-real-pass-123",  # pragma: allowlist secret
        "API_DATABASE_URL": GOOD_BLOCK["MIGRATE_DATABASE_URL"],
    }
    problems, _ = server_problems(spoiled)
    assert problems == [
        f"POSTGRES_APP_PASSWORD: в образце не заглушка {PLACEHOLDER}",
        "API_DATABASE_URL: в адресе не роль audit_app",
    ]
    assert not any("looks-real" in p or "audit_migrate" in p for p in problems)


COMPOSE = dbtools.ROOT / "docker-compose.dev.yml"


def test_a13_dev_database_is_the_compose_one_on_5462() -> None:
    """A13: база разработки по умолчанию — из docker-compose.dev.yml на 5462, стек назван явно."""
    compose = COMPOSE.read_text(encoding="utf-8")
    assert "@localhost:5462/" in config.StorageSettings().database_url.get_secret_value()
    assert '"127.0.0.1:5462:5432"' in compose
    assert re.search(r"(?m)^name: ai-geo-audit-dev$", compose)
