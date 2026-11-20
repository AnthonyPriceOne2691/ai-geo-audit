"""Проверка старта: в бою называет все негодные настройки разом, в разработке молчит."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import dbtools
import pytest

from ai_geo_audit import config
from ai_geo_audit.config import startup_checks as sc

# pragma: allowlist nextline secret
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
        "API_TRUSTED_PROXIES": "10.9.9.9",
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
        api=config.ApiSettings(),
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
        # pragma: allowlist nextline secret
        "JWT_SECRET": "CHANGE_ME_" + "x" * 40,
        # pragma: allowlist nextline secret
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
    """A3, A33: миграциям хватает базы; воркеру не нужен секрет входа, api — нужно всё, и ещё
    адреса прокси (A33)."""
    assert names(prod(monkeypatch, "migrate", DATABASE_URL=DSN)) == set()
    worker = {"REDIS_URL", "KEYS_MASTER_KEY_FILE"}
    assert names(prod(monkeypatch, "worker", DATABASE_URL=DSN)) == worker
    api = worker | {"JWT_SECRET", "API_TRUSTED_PROXIES"}
    assert names(prod(monkeypatch, "api", DATABASE_URL=DSN)) == api


EXPLICIT = "API_TRUSTED_PROXIES — в бою задаётся явно: умолчание ведёт на машину разработчика"


def test_a33_prod_api_needs_trusted_proxies_explicitly(
    monkeypatch: pytest.MonkeyPatch, master_key: str
) -> None:
    """A33: `APP_ENV=prod`, всё задано, кроме `API_TRUSTED_PROXIES`, — процесс `api` не
    стартует, строка отказа ровно «API_TRUSTED_PROXIES — в бою задаётся явно: умолчание ведёт на
    машину разработчика»; у `worker` и `migrate` этой строки нет."""
    env = {k: v for k, v in full_env(master_key).items() if k != "API_TRUSTED_PROXIES"}
    with pytest.raises(sc.ConfigError) as refusal:
        sc.check_startup("api", prod(monkeypatch, "api", **env))
    assert [line.strip() for line in str(refusal.value).splitlines()[1:]] == [EXPLICIT]
    assert [p for p in ("worker", "migrate") if names(prod(monkeypatch, p, **env))] == []


def test_a4_starts_without_external_keys(monkeypatch: pytest.MonkeyPatch, master_key: str) -> None:
    """A4: ключи OpenAI, DataForSEO и Ahrefs для старта не нужны и конфигом не читаются."""
    assert prod(monkeypatch, "api", **full_env(master_key)) == []
    providers = ("OPENAI", "DATAFORSEO", "AHREFS")
    assert not [name for name in config.env_names() if any(p in name for p in providers)]


@pytest.mark.parametrize("env", ["dev", "test"])
def test_outside_prod_checks_are_silent(monkeypatch: pytest.MonkeyPatch, env: str) -> None:
    """A1: разработка и тесты стартуют без единой настройки."""
    monkeypatch.setenv("APP_ENV", env)
    assert sc.problems("api", app=config.AppSettings()) == []


UNENCODED = ["pa@ss-word-123456", "pa#ss-word-123456", "pa?ss-word-123456", "pa/ss-word-123456"]
PROCESSES: list[sc.Process] = ["api", "worker", "migrate"]


def db(password: str | None, rest: str = "db:5432/audit", user: str = "audit") -> str:
    auth = user if password is None else f"{user}:{password}"
    return f"postgresql+asyncpg://{auth}@{rest}"


@pytest.mark.parametrize("password", UNENCODED)
@pytest.mark.parametrize("process", PROCESSES)
def test_a21_unencoded_sign_in_the_db_password_is_refused(
    monkeypatch: pytest.MonkeyPatch, master_key: str, process: sc.Process, password: str
) -> None:
    """A21: в бою в пароле базы не закодирован `@`, `#`, `?` или `/` — `api`, `worker` и `migrate`
    не стартуют со своей причиной, а не «короче 12»; ни адреса, ни куска пароля в отказе нет."""
    found = prod(monkeypatch, process, **full_env(master_key) | {"DATABASE_URL": db(password)})
    assert found == [sc.Problem("DATABASE_URL", sc.DB_URL_UNENCODED_CHARS)]
    assert "ss-word" not in sc.DB_URL_UNENCODED_CHARS and "audit" not in sc.DB_URL_UNENCODED_CHARS


GOOD = [
    db("pass-word-123456", "DB.Internal:5432/audit"),
    db("pass-word-123456", "[::1]:5432/audit"),
    db("pass-word-123456", "db/audit"),
    db("pass-word-123456", "db:5432/audit?ssl=require"),
    db("pa%40ss-word-123456"),
    db("pass-word-123456", "db:5432/au%2Fdit", user="au%40dit"),
    db(quote("пароль-длин1")),
]


@pytest.mark.parametrize("url", GOOD)
def test_a21_good_db_urls_pass_both_parsers(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    """A21: годные адреса — заглавные в имени хоста, IPv6 в скобках, без порта, параметры после
    «?», `%40` в пароле, имя и база в процентной записи, пароль кириллицей в 12 знаков — старт:
    сверка двух разборщиков не даёт ложного отказа."""
    assert prod(monkeypatch, "migrate", DATABASE_URL=url) == []


SHORT = sc.Problem("DATABASE_URL", f"пароль базы короче {sc.MIN_DB_PASSWORD} знаков")


@pytest.mark.parametrize("process", PROCESSES)
def test_a21_db_password_of_11_signs_is_refused_and_12_starts(
    monkeypatch: pytest.MonkeyPatch, master_key: str, process: sc.Process
) -> None:
    """A21: в бою пароль базы 11 знаков — `api`, `worker` и `migrate` не стартуют с причиной
    «пароль базы короче 12 знаков»; 12 — старт."""
    env = full_env(master_key)
    assert prod(monkeypatch, process, **env | {"DATABASE_URL": db("a" * 11)}) == [SHORT]
    assert prod(monkeypatch, process, **env | {"DATABASE_URL": db("a" * 12)}) == []


@pytest.mark.parametrize(
    "url",
    [
        db("ab%40cdefghij"),
        db(quote("пароль-длин")),
        db(None),
        # pragma: allowlist nextline secret
        "postgresql+asyncpg://audit:audit@localhost:5462/audit",
    ],
    ids=["percent-13-is-11", "cyrillic-11-is-22-bytes", "no-password", "dev-compose-password"],
)
def test_a21_short_db_passwords_are_counted_after_decoding(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    """A21: длина — по знакам после раскодирования: `ab%40cdefghij` (13 в записи, 11 знаков),
    кириллица из 11 знаков (22 байта), адрес без пароля и публичный пароль разработки из
    `docker-compose.dev.yml` — отказ «короче 12»."""
    assert prod(monkeypatch, "migrate", DATABASE_URL=url) == [SHORT]


def test_a21_migrate_refuses_an_unencoded_password_without_printing_it() -> None:
    """A21: процесс `migrate` в бою с паролем `pa@ss-word-123456` — код не 0, причина словами, без
    трассировки: слова причины стоят и в последней строке трассировки (порча E10 проверяющего);
    ни куска пароля в журнале нет."""
    url = db("pa@ss-word-123456", f"127.0.0.1:5462/{dbtools.own_name()}")
    run = dbtools.alembic("upgrade", "head", extra={"APP_ENV": "prod", "DATABASE_URL": url})
    assert run.returncode != 0
    assert sc.DB_URL_UNENCODED_CHARS in run.stderr
    assert "Traceback" not in run.stderr, run.stderr[-1500:]
    assert "ss-word-123456" not in run.stderr
