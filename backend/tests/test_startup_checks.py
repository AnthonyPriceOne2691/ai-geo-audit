"""Проверка старта: в бою называет все негодные настройки разом, в разработке молчит."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import dbtools
import masterkeys
import pytest

from ai_geo_audit import config
from ai_geo_audit.config import startup_checks as sc

#: Роли процессов литералами: тест сверяет таблицу `startup_checks.DB_ROLES`, а не берёт её (A174).
ROLE: dict[sc.Process, str] = {
    "api": "audit_app",
    "worker": "audit_app",
    "migrate": "audit_migrate",
    "reference": "audit_app",
}
# pragma: allowlist nextline secret
DSN = "postgresql+asyncpg://audit_app:pw-1234567890@db:5432/audit"
# pragma: allowlist nextline secret
DSN_MIGRATE = "postgresql+asyncpg://audit_migrate:pw-1234567890@db:5432/audit"
#: Пара бота тревог (A192): выдуманный токен и номер группового чата — с минусом.
# pragma: allowlist nextline secret
BOT_TOKEN = "123456789:test-bot-token-value-0001"
CHAT_ID = "-1001234567890"
PAIR = ("ALERT_TELEGRAM_BOT_TOKEN", "ALERT_TELEGRAM_CHAT_ID")


@pytest.fixture
def master_key(master_file: masterkeys.MasterFile) -> str:
    """Путь к годному файлу мастер-ключа — общей фикстуры (b1:457)."""
    return str(master_file.path)


def queue(password: str | None, rest: str = "redis:6379/0") -> str:
    """Адрес очереди (A175): пароль — после двоеточия без имени, как в образце окружения."""
    auth = "" if password is None else f":{password}@"
    return f"redis://{auth}{rest}"


def full_env(master_key: str) -> dict[str, str]:
    return {
        "DATABASE_URL": DSN,
        "REDIS_URL": queue("pw-1234567890"),
        "JWT_SECRET": "a" * 64,
        "KEYS_MASTER_KEY_FILE": master_key,
        "API_TRUSTED_PROXIES": "10.9.9.9",
        "ALERT_TELEGRAM_BOT_TOKEN": BOT_TOKEN,
        "ALERT_TELEGRAM_CHAT_ID": CHAT_ID,
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
        alerts=config.AlertSettings(),
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


MASTER_KEY_FOR = "мастер-ключ шифрования ключей сервисов"


@pytest.mark.parametrize(
    ("value", "why"),
    [
        ("", f"не задано — {MASTER_KEY_FOR}"),
        ("CHANGE_ME", f"стоит заглушка из .env.example — {MASTER_KEY_FOR}"),
    ],
    ids=["empty", "placeholder"],
)
def test_a3_master_key_reason_is_compared_whole(
    monkeypatch: pytest.MonkeyPatch, master_key: str, value: str, why: str
) -> None:
    """A3, L136: пустой путь к мастер-ключу и заглушка `.env.example` у api и worker — причина
    целиком, с назначением ключа: `in` и `startswith` порчи текста не держат (L124)."""
    env = full_env(master_key) | {"KEYS_MASTER_KEY_FILE": value}
    for process in ("api", "worker"):
        assert prod(monkeypatch, process, **env) == [sc.Problem("KEYS_MASTER_KEY_FILE", why)]


def test_a3_processes_need_different_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """A3, A33, A192: миграциям хватает базы; воркеру не нужен секрет входа, api — нужно всё, и
    ещё адреса прокси (A33) и пара бота тревог (A192)."""
    assert names(prod(monkeypatch, "migrate", DATABASE_URL=DSN_MIGRATE)) == set()
    worker = {"REDIS_URL", "KEYS_MASTER_KEY_FILE"}
    assert names(prod(monkeypatch, "worker", DATABASE_URL=DSN)) == worker
    api = worker | {"JWT_SECRET", "API_TRUSTED_PROXIES", *PAIR}
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
    worker = names(prod(monkeypatch, "worker", **env))
    migrate = names(prod(monkeypatch, "migrate", **env | {"DATABASE_URL": DSN_MIGRATE}))
    assert (worker, migrate) == (set(), set())


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
PROCESSES: list[sc.Process] = ["api", "worker", "migrate", "reference"]


def db(password: str | None, rest: str = "db:5432/audit", user: str = "audit_migrate") -> str:
    auth = user if password is None else f"{user}:{password}"
    return f"postgresql+asyncpg://{auth}@{rest}"


@pytest.mark.parametrize("password", UNENCODED)
@pytest.mark.parametrize("process", PROCESSES)
def test_a21_unencoded_sign_in_the_db_password_is_refused(
    monkeypatch: pytest.MonkeyPatch, master_key: str, process: sc.Process, password: str
) -> None:
    """A21: в бою в пароле базы не закодирован `@`, `#`, `?` или `/` — `api`, `worker` и `migrate`
    не стартуют со своей причиной, а не «короче 12»; ни адреса, ни куска пароля в отказе нет."""
    url = db(password, user=ROLE[process])
    found = prod(monkeypatch, process, **full_env(master_key) | {"DATABASE_URL": url})
    assert found == [sc.Problem("DATABASE_URL", sc.DB_URL_UNENCODED_CHARS)]
    assert "ss-word" not in sc.DB_URL_UNENCODED_CHARS and "audit" not in sc.DB_URL_UNENCODED_CHARS


GOOD = [
    db("pass-word-123456", "DB.Internal:5432/audit"),
    db("pass-word-123456", "[::1]:5432/audit"),
    db("pass-word-123456", "db/audit"),
    db("pass-word-123456", "db:5432/audit?ssl=require"),
    db("pa%40ss-word-123456"),
    db("pass-word-123456", "db:5432/au%2Fdit", user="audit%5Fmigrate"),
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
    env, user = full_env(master_key), ROLE[process]
    assert prod(monkeypatch, process, **env | {"DATABASE_URL": db("a" * 11, user=user)}) == [SHORT]
    assert prod(monkeypatch, process, **env | {"DATABASE_URL": db("a" * 12, user=user)}) == []


@pytest.mark.parametrize(
    "url",
    [
        db("ab%40cdefghij"),
        db(quote("парольдлина")),
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


#: Чужие роли по процессам (A174): у сервиса — роль миграций и суперпользователь образа; имена,
#: похожие на роль, — тоже чужие: сверка с учётом регистра и целиком (находка 4 проверяющей).
FOREIGN: list[tuple[sc.Process, str]] = [
    ("api", "audit_migrate"),
    ("api", "audit"),
    ("worker", "audit_migrate"),
    ("worker", "audit"),
    ("migrate", "audit_app"),
    ("api", "AUDIT_APP"),
    ("migrate", "audit_migrate_x"),
    ("reference", "audit_migrate"),
]
LONG = "pw-1234567890"


def foreign_role(process: sc.Process) -> sc.Problem:
    return sc.Problem("DATABASE_URL", f"в адресе чужая роль базы — нужна {ROLE[process]}")


@pytest.mark.parametrize(("process", "user"), FOREIGN, ids=[f"{p}-{u}" for p, u in FOREIGN])
def test_a174_foreign_db_role_is_refused_without_the_found_name(
    monkeypatch: pytest.MonkeyPatch, master_key: str, process: sc.Process, user: str
) -> None:
    """A174: в бою у `api` и `worker` в адресе базы роль `audit_migrate` или суперпользователь
    `audit`, у `migrate` — `audit_app`; похожие на роль `AUDIT_APP` у `api` и `audit_migrate_x` у
    `migrate`: отказ у `DATABASE_URL` ровно «в адресе чужая роль базы — нужна <роль процесса>»;
    найденного имени и пароля в тексте отказа нет."""
    env = full_env(master_key) | {"DATABASE_URL": db(LONG, user=user)}
    found = prod(monkeypatch, process, **env)
    assert found == [foreign_role(process)]
    with pytest.raises(sc.ConfigError) as refusal:
        sc.check_startup(process, found)
    assert str(refusal.value).splitlines()[1:] == [f"  DATABASE_URL — {foreign_role(process).why}"]
    assert LONG not in str(refusal.value)


@pytest.mark.parametrize("process", PROCESSES)
def test_a174_own_db_role_starts(
    monkeypatch: pytest.MonkeyPatch, master_key: str, process: sc.Process
) -> None:
    """A174: верные роли — старт: `api` и `worker` с `audit_app`, `migrate` с `audit_migrate`."""
    env = full_env(master_key) | {"DATABASE_URL": db(LONG, user=ROLE[process])}
    assert prod(monkeypatch, process, **env) == []


def test_a174_role_name_is_compared_after_decoding(
    monkeypatch: pytest.MonkeyPatch, master_key: str
) -> None:
    """A174: имя роли в процентной записи `audit%5Fapp` у `api` — старт: имя сверяется после
    раскодирования, а не по записи."""
    env = full_env(master_key) | {"DATABASE_URL": db(LONG, user="audit%5Fapp")}
    assert prod(monkeypatch, "api", **env) == []


def test_a174_password_in_the_name_place_is_short_and_not_printed(
    monkeypatch: pytest.MonkeyPatch, master_key: str
) -> None:
    """A174: пароль на месте имени — `postgresql+asyncpg://<пароль из 16 знаков>@db:5432/audit` —
    отказ «пароль базы короче 12 знаков», а не о роли; пароля в тексте нет."""
    word = "Name-Pass-123456"
    url = f"postgresql+asyncpg://{word}@db:5432/audit"
    found = prod(monkeypatch, "api", **full_env(master_key) | {"DATABASE_URL": url})
    assert found == [SHORT]
    assert word not in found[0].why


def test_a174_one_reason_per_variable_role_goes_last(
    monkeypatch: pytest.MonkeyPatch, master_key: str
) -> None:
    """A174 и A21: одна переменная — одна причина, роль — последней: чужая роль с паролем в 11
    знаков — «короче 12», с незакодированным «@» в пароле — причина разборщиков."""
    env = full_env(master_key)
    assert prod(monkeypatch, "api", **env | {"DATABASE_URL": db("a" * 11, user="audit")}) == [SHORT]
    unencoded = db("pa@ss-word-123456", user="audit")
    found = prod(monkeypatch, "api", **env | {"DATABASE_URL": unencoded})
    assert found == [sc.Problem("DATABASE_URL", sc.DB_URL_UNENCODED_CHARS)]


def test_a174_outside_prod_a_foreign_role_is_silent(monkeypatch: pytest.MonkeyPatch) -> None:
    """A174: `APP_ENV=dev` с чужой ролью — молчит."""
    monkeypatch.setenv("APP_ENV", "dev")
    monkeypatch.setenv("DATABASE_URL", db(LONG, user="audit"))
    assert sc.problems("api", app=config.AppSettings(), storage=config.StorageSettings()) == []


def test_a174_process_roles_come_from_one_table() -> None:
    """A174: роли процессов — одна таблица `startup_checks.DB_ROLES`; её же сверяет A179."""
    assert sc.DB_ROLES == ROLE


#: Процессы, которым нужен адрес очереди (A175); у `migrate` его нет в списке.
QUEUE_PROCESSES: list[sc.Process] = ["api", "worker"]
QUEUE_SHORT = sc.Problem("REDIS_URL", f"пароль очереди короче {sc.MIN_DB_PASSWORD} знаков")
#: Пароль на месте имени — вход без двоеточия: адрес читается как имя без пароля.
NAME_PLACE = "Name-Pass-123456"
SHORT_QUEUES = {
    "no-password": queue(None),
    "11-signs": queue("a" * 11),
    "percent-13-is-11": queue("ab%40cdefghij"),
    "cyrillic-11-is-22-bytes": queue(quote("парольдлина")),
    "password-in-name-place": f"redis://{NAME_PLACE}@redis:6379/0",
}
GOOD_QUEUES = {
    "12-signs": queue("a" * 12),
    "upper-host": queue("pass-word-123456", "Redis.Internal:6379/0"),
    "ipv6": queue("pass-word-123456", "[::1]:6379/0"),
    "no-port": queue("pass-word-123456", "redis/0"),
    "query": queue("pass-word-123456", "redis:6379/0?socket_timeout=5"),
    "percent-at": queue("pa%40ss-word-123456"),
    "cyrillic-12": queue(quote("пароль-длин1")),
}


@pytest.mark.parametrize("url", list(SHORT_QUEUES.values()), ids=list(SHORT_QUEUES))
@pytest.mark.parametrize("process", QUEUE_PROCESSES)
def test_a175_short_queue_password_is_refused_counted_after_decoding(
    monkeypatch: pytest.MonkeyPatch, master_key: str, process: sc.Process, url: str
) -> None:
    """A175: в бою у `api` и `worker` адрес очереди без пароля, с паролем в 11 знаков,
    `ab%40cdefghij` (13 в записи, 11 после раскодирования), кириллицей в 11 знаков (22 байта) и с
    паролем на месте имени — отказ ровно «пароль очереди короче 12 знаков»: длина — по знакам после
    раскодирования, а не по байтам; ни адреса, ни куска пароля в тексте нет."""
    found = prod(monkeypatch, process, **full_env(master_key) | {"REDIS_URL": url})
    assert found == [QUEUE_SHORT]
    with pytest.raises(sc.ConfigError) as refusal:
        sc.check_startup(process, found)
    assert str(refusal.value).splitlines()[1:] == [f"  REDIS_URL — {QUEUE_SHORT.why}"]
    for piece in ("redis:6379", "aaaaaaaaaaa", "cdefghij", "парольдлин", "%D0", NAME_PLACE):
        assert piece not in str(refusal.value)


@pytest.mark.parametrize("password", UNENCODED)
@pytest.mark.parametrize("process", QUEUE_PROCESSES)
def test_a175_unencoded_sign_in_the_queue_password_is_refused(
    monkeypatch: pytest.MonkeyPatch, master_key: str, process: sc.Process, password: str
) -> None:
    """A175: в бою в пароле очереди не закодирован `@`, `#`, `?` или `/` — у `api` и `worker`
    причина «в пароле незакодированные знаки — закодируйте @ / ? # как %40 %2F %3F %23», а не
    «короче 12»: та же сверка двух разборщиков и тот же текст, что у адреса базы (A21)."""
    found = prod(monkeypatch, process, **full_env(master_key) | {"REDIS_URL": queue(password)})
    assert found == [sc.Problem("REDIS_URL", sc.DB_URL_UNENCODED_CHARS)]


@pytest.mark.parametrize("url", list(GOOD_QUEUES.values()), ids=list(GOOD_QUEUES))
@pytest.mark.parametrize("process", QUEUE_PROCESSES)
def test_a175_queue_password_of_12_signs_and_good_urls_start(
    monkeypatch: pytest.MonkeyPatch, master_key: str, process: sc.Process, url: str
) -> None:
    """A175: 12 знаков и годные адреса — заглавные в имени хоста, IPv6 в скобках, без порта,
    параметры после «?», `%40` в пароле, пароль кириллицей в 12 знаков — старт у `api` и `worker`:
    сверка двух разборщиков не даёт ложного отказа (держится до кода)."""
    assert prod(monkeypatch, process, **full_env(master_key) | {"REDIS_URL": url}) == []


@pytest.mark.parametrize("process", QUEUE_PROCESSES)
def test_a175_queue_placeholder_is_one_reason(
    monkeypatch: pytest.MonkeyPatch, master_key: str, process: sc.Process
) -> None:
    """A175: заглушка `CHANGE_ME` на месте пароля очереди — одна причина, заглушка; «короче 12» с
    ней не идёт (держится до кода: заглушку ловит `_explicit` Ч1.2в)."""
    env = full_env(master_key) | {"REDIS_URL": queue(sc.PLACEHOLDER)}
    assert prod(monkeypatch, process, **env) == [
        sc.Problem("REDIS_URL", "стоит заглушка из .env.example — подключение")
    ]


def test_a175_migrate_does_not_check_the_queue(
    monkeypatch: pytest.MonkeyPatch, master_key: str
) -> None:
    """A175: `migrate` с адресом очереди без пароля — старт: адреса очереди нет в списке процесса
    (держится до кода)."""
    env = full_env(master_key) | {"DATABASE_URL": DSN_MIGRATE, "REDIS_URL": queue(None)}
    assert prod(monkeypatch, "migrate", **env) == []
    assert "REDIS_URL" not in sc.required("migrate")


@pytest.mark.parametrize("case", list(masterkeys.BAD_FILES))
def test_a221_prod_api_and_worker_refuse_a_bad_master_key_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, master_key: str, case: str
) -> None:
    """A221: в бою у api и worker негодный файл мастер-ключа (любой случай A220) — причина с именем
    `KEYS_MASTER_KEY_FILE` и номером строки, без содержимого файла; у migrate причины нет:
    мастер-ключ ему не нужен."""
    row = masterkeys.line()
    reason, content = masterkeys.bad_file(case, row)
    bad = tmp_path / "bad.key"
    bad.write_bytes(content)
    env = full_env(master_key) | {"KEYS_MASTER_KEY_FILE": str(bad)}
    for process in ("api", "worker"):
        found = prod(monkeypatch, process, **env)
        assert found == [sc.Problem("KEYS_MASTER_KEY_FILE", reason)]
        with pytest.raises(sc.ConfigError) as refusal:
            sc.check_startup(process, found)
        assert str(refusal.value).splitlines()[1:] == [f"  KEYS_MASTER_KEY_FILE — {reason}"]
        assert row not in str(refusal.value)
    assert prod(monkeypatch, "migrate", **env | {"DATABASE_URL": DSN_MIGRATE}) == []


def test_a221_good_master_key_file_gives_no_reason(
    monkeypatch: pytest.MonkeyPatch, master_key: str
) -> None:
    """A221: годный файл — у api и worker причины нет; вне боя проверка молчит."""
    for process in ("api", "worker", "migrate"):
        env = full_env(master_key) | {"DATABASE_URL": db(LONG, user=ROLE[process])}
        assert prod(monkeypatch, process, **env) == []
    monkeypatch.setenv("APP_ENV", "dev")
    assert sc.problems("worker", app=config.AppSettings()) == []


def test_a221_closed_file_and_folder_are_reasons_in_words(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, master_key: str
) -> None:
    """A221, соседние случаи: файл без прав на чтение (на сервере — uid 10001) — причина словами
    «файл не читается», каталог вместо файла — «файла нет»: вход ловит только `ConfigError`
    (L123), голая ошибка ОС ушла бы трассировкой."""
    closed = masterkeys.write(tmp_path / "closed.key", [masterkeys.line()])
    closed.chmod(0)
    folder = tmp_path / "folder"
    folder.mkdir()
    cases = {closed: f"файл не читается: {closed}", folder: f"файла нет: {folder}"}
    for path, reason in cases.items():
        env = full_env(master_key) | {"KEYS_MASTER_KEY_FILE": str(path)}
        assert prod(monkeypatch, "worker", **env) == [sc.Problem("KEYS_MASTER_KEY_FILE", reason)]


TOKEN_FOR = "токен бота тревог"
CHAT_FOR = "номер чата тревог"


def without(env: dict[str, str], *gone: str) -> dict[str, str]:
    return {name: value for name, value in env.items() if name not in gone}


def test_a192_prod_api_names_the_missing_bot_pair(
    monkeypatch: pytest.MonkeyPatch, master_key: str
) -> None:
    """A192: в бою у api нет токена бота и номера чата — api не стартует; причина у каждого имени
    целиком (L124), токен — первым."""
    assert prod(monkeypatch, "api", **without(full_env(master_key), *PAIR)) == [
        sc.Problem("ALERT_TELEGRAM_BOT_TOKEN", f"не задано — {TOKEN_FOR}"),
        sc.Problem("ALERT_TELEGRAM_CHAT_ID", f"не задано — {CHAT_FOR}"),
    ]


def test_a192_placeholder_token_is_refused(
    monkeypatch: pytest.MonkeyPatch, master_key: str
) -> None:
    """A192: токен — заглушка `.env.example`: отказ с именем, причина целиком."""
    env = full_env(master_key) | {"ALERT_TELEGRAM_BOT_TOKEN": "CHANGE_ME"}
    assert prod(monkeypatch, "api", **env) == [
        sc.Problem("ALERT_TELEGRAM_BOT_TOKEN", f"стоит заглушка из .env.example — {TOKEN_FOR}")
    ]


#: Не целое: слово, пробел внутри и по краю, дробь, знак без цифр, шестнадцатеричная запись,
#: цифры не ASCII — `int()` их принял бы.
NOT_INTEGER = {
    "слово": "abc",
    "пробел": "12 34",
    "пробел-с-края": " 123",
    "дробь": "1.5",
    "минус": "-",
    "минус-в-конце": "12-",
    "hex": "0x1F",
    "не-ascii": "١٢٣",
}


@pytest.mark.parametrize("chat", list(NOT_INTEGER.values()), ids=list(NOT_INTEGER))
def test_a192_chat_that_is_not_an_integer_is_refused_without_its_value(
    monkeypatch: pytest.MonkeyPatch, master_key: str, chat: str
) -> None:
    """A192: номер чата — не целое: отказ с именем; причина целиком, значения в ней нет."""
    env = full_env(master_key) | {"ALERT_TELEGRAM_CHAT_ID": chat}
    assert prod(monkeypatch, "api", **env) == [
        sc.Problem("ALERT_TELEGRAM_CHAT_ID", f"не целое — {CHAT_FOR}")
    ]


@pytest.mark.parametrize("chat", [CHAT_ID, "123456789"], ids=["group", "private"])
def test_a192_group_and_private_chat_numbers_start(
    monkeypatch: pytest.MonkeyPatch, master_key: str, chat: str
) -> None:
    """A192: номер группы — с минусом, личного чата — без; оба целые, api стартует."""
    env = full_env(master_key) | {"ALERT_TELEGRAM_CHAT_ID": chat}
    assert prod(monkeypatch, "api", **env) == []


def test_a192_worker_and_migrate_start_without_the_bot_pair(
    monkeypatch: pytest.MonkeyPatch, master_key: str
) -> None:
    """A192: пара — только у api; воркер и миграции без неё стартуют, а в их окружении её держит
    правило `foreign-secret`."""
    env = without(full_env(master_key), *PAIR)
    assert prod(monkeypatch, "worker", **env) == []
    assert prod(monkeypatch, "migrate", **env | {"DATABASE_URL": DSN_MIGRATE}) == []


def test_a192_token_is_a_secret_by_type(monkeypatch: pytest.MonkeyPatch) -> None:
    """A192: токен — `SecretStr`: ни `repr`, ни `model_dump()` настроек его не показывают, номер
    чата — обычная строка."""
    monkeypatch.setenv("ALERT_TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    monkeypatch.setenv("ALERT_TELEGRAM_CHAT_ID", CHAT_ID)
    settings = config.AlertSettings()
    assert (settings.bot_token.get_secret_value(), settings.chat_id) == (BOT_TOKEN, CHAT_ID)
    assert BOT_TOKEN not in repr(settings)
    assert BOT_TOKEN not in str(settings.model_dump())
