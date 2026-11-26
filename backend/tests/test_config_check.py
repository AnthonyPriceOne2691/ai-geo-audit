"""Разбор конфига без старта (A189, первая строка): `python -m ai_geo_audit.ops.config_check
<процесс>` — тот же приговор, что даст старт процесса, без старта. Случаи A189 — настоящая
команда подпроцессом с окружением с нуля: ошибку импорта конфига `try` внутри функции не увидит,
если пакет команды сам грузит конфиг (`-m` импортирует пакеты-родители раньше модуля). Ветки
функции — ещё и в процессе: под mutmut подпроцесс идёт на исходном коде (L6, L12)."""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from types import ModuleType
from typing import get_args

import dbtools
import masterkeys
import pytest
from pydantic import ValidationError

import ai_geo_audit
from ai_geo_audit import config
from ai_geo_audit.config import startup_checks as sc
from ai_geo_audit.refusal import invalid_settings

MODULE = "ai_geo_audit.ops.config_check"
#: Роли процессов литералами: адрес базы с чужой ролью в бою не даёт старта (A174).
ROLE = {"api": "audit_app", "migrate": "audit_migrate"}
#: Пароль адресов — не короче 12 знаков; короткий — 8. Слово вида «секрет» тесту не нужно.
WORD = "pw-check-1234567890"
SHORT = "pw-short"
BOT = "123456789:check-bot-value-0001"
CHAT = "-1001234567890"
#: Отказ проверки старта у адреса с коротким паролем и у заглушки секрета подписи — целиком.
SHORT_DB = "DATABASE_URL — пароль базы короче 12 знаков"
PLACEHOLDER_JWT = (
    "JWT_SECRET — стоит заглушка из .env.example — подпись пропусков: openssl rand -hex 32"
)
#: Отказ импорта конфига: имя и причина pydantic без введённого значения (L30).
NOT_INT = (
    "MIGRATION_LOCK_WAIT_SECONDS — Input should be a valid integer, unable to parse string as an "
    "integer"
)
POOL_HIGH = "DB_POOL_SIZE — Input should be less than or equal to 50"
#: Подпроцесс под стражем: соединение, новый процесс или импорт сервера и клиентов базы и Redis
#: — выход 97 сразу, раньше любого `except` в коде. Остальное — как у `python -m`.
GUARDED = """
import os, runpy, sys
EVENTS = ("socket.connect", "socket.getaddrinfo", "subprocess.Popen", "os.posix_spawn", "os.exec",
          "os.system")
MODULES = ("uvicorn", "asyncpg", "redis", "ai_geo_audit.api")
def guard(event, args):
    if event in EVENTS or (event == "import" and str(args[0]).startswith(MODULES)):
        sys.stderr.write("ЗАПРЕЩЕНО: " + event + " " + str(args[0]) + "\\n")
        sys.stderr.flush()
        os._exit(97)
sys.addaudithook(guard)
sys.argv = ["config_check", *sys.argv[1:]]
runpy.run_module("ai_geo_audit.ops.config_check", run_name="__main__", alter_sys=True)
"""


@pytest.fixture
def command() -> ModuleType:
    """Модуль команды: нет модуля — красный каждый тест, а не сбор файла."""
    return importlib.import_module(MODULE)


@pytest.fixture
def master_key(master_file: masterkeys.MasterFile) -> str:
    """Путь к годному файлу мастер-ключа — общей фикстуры (b1:457)."""
    return str(master_file.path)


def battle(master_key: str, process: str, **changes: str) -> dict[str, str]:
    """Годный бой процесса: адреса с ролью процесса и паролем не короче 12, секрет подписи — 64
    знака, мастер-ключ — файл фикстуры, пара бота тревог; `changes` — поверх."""
    auth = f"{ROLE[process]}:{WORD}"
    env = {
        "APP_ENV": "prod",
        "DATABASE_URL": f"postgresql+asyncpg://{auth}@db:5432/audit",
        "REDIS_URL": f"redis://:{WORD}@redis:6379/0",
        "JWT_SECRET": "a" * 64,
        "KEYS_MASTER_KEY_FILE": master_key,
        "API_TRUSTED_PROXIES": "10.9.9.9",
        "ALERT_TELEGRAM_BOT_TOKEN": BOT,
        "ALERT_TELEGRAM_CHAT_ID": CHAT,
    }
    return env | changes


def short_and_placeholder(master_key: str, process: str) -> dict[str, str]:
    """A189: бой, у адреса базы пароль короче 12 знаков, у `JWT_SECRET` — заглушка образца."""
    auth = f"{ROLE[process]}:{SHORT}"
    return battle(
        master_key,
        process,
        DATABASE_URL=f"postgresql+asyncpg://{auth}@db:5432/audit",
        JWT_SECRET=f"{sc.PLACEHOLDER}_jwt",
    )


def scratch(extra: dict[str, str]) -> dict[str, str]:
    """Окружение подпроцесса с нуля, как `dbtools.alembic_env`: без `.env`, без переменных
    разработчика и без `MUTANT_UNDER_TEST`. `PYTHONPATH` и `COVERAGE_*` проходят — порчам на копии
    `src` и покрытию; без своего `PYTHONPATH` пакет — исходный `backend/src` через `.pth`."""
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONUTF8": "1", "ENV_FILE": os.devnull}
    env.update(
        {k: v for k, v in os.environ.items() if k == "PYTHONPATH" or k.startswith("COVERAGE_")}
    )
    return env | extra


def run(argv: list[str], extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """`python <argv>` из `backend/` с окружением с нуля."""
    return subprocess.run(
        [sys.executable, *argv],
        cwd=dbtools.BACKEND,
        env=scratch(extra),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def clean(text: str, *values: str) -> None:
    """L29: во всём выводе нет трассировки, текста pydantic о значении и самих значений."""
    assert "Traceback" not in text, text[-1500:]
    assert "input_value" not in text, text[-1500:]
    assert [value for value in values if value in text] == [], text[-1500:]


@pytest.mark.parametrize("process", ["api", "migrate"])
def test_a189_good_battle_settings_pass_with_the_count_of_names(
    master_key: str, process: str
) -> None:
    """A189: `python -m ai_geo_audit.ops.config_check api` и `… migrate`, бой и годные настройки
    — код 0 и «проверено имён: N» — все имена конфига; поток ошибок пуст."""
    done = run(["-m", MODULE, process], battle(master_key, process))
    names = len(config.env_names())
    assert (done.returncode, done.stderr) == (0, ""), done.stdout + done.stderr
    expected = f"Конфиг процесса «{process}» годен, режим prod: проверено имён: {names}\n"
    assert done.stdout == expected
    clean(done.stdout, WORD, BOT)


@pytest.mark.parametrize(
    ("process", "lines"),
    [("api", [SHORT_DB, PLACEHOLDER_JWT]), ("migrate", [SHORT_DB])],
    ids=["api", "migrate"],
)
def test_a189_bad_battle_settings_are_named_at_once_without_values(
    master_key: str, process: str, lines: list[str]
) -> None:
    """A189: бой, у `JWT_SECRET` заглушка, у адреса базы пароль короче 12 знаков — код 1, все
    негодные имена процесса разом с причинами: секрет подписи нужен только api. Без `Traceback`
    и введённых значений (L29)."""
    done = run(["-m", MODULE, process], short_and_placeholder(master_key, process))
    head = f"Процесс «{process}» не стартует в боевом режиме:"
    assert done.returncode == 1, done.stdout + done.stderr
    assert done.stderr == "\n".join([head, *(f"  {line}" for line in lines)]) + "\n"
    assert done.stdout == ""
    clean(done.stderr, SHORT, sc.PLACEHOLDER)


@pytest.mark.parametrize("process", ["api", "migrate"])
def test_a189_value_refused_at_config_import_is_named_without_value(
    master_key: str, process: str
) -> None:
    """A189: `MIGRATION_LOCK_WAIT_SECONDS=abc` — значение, которое конфиг отвергает уже при
    импорте, — настоящей командой подпроцессом: код 1, имя и причина словами, без `Traceback` и
    значения (L29, L30). Второе негодное поле того же домена (`DB_POOL_SIZE=777`) — в том же
    отказе: все поля упавшего домена разом. Импорт конфига в пакете `ops` или наверху модуля
    команды вне `try` — здесь красное (условие третьей проверяющей, 2026-11-26)."""
    env = battle(master_key, process, MIGRATION_LOCK_WAIT_SECONDS="abc", DB_POOL_SIZE="777")
    done = run(["-m", MODULE, process], env)
    head = f"Процесс «{process}» не стартует, негодные настройки:"
    assert done.returncode == 1, done.stdout + done.stderr
    assert done.stderr == f"{head}\n  {POOL_HIGH}\n  {NOT_INT}\n"
    assert done.stdout == ""
    clean(done.stderr, "abc", "777")


@pytest.mark.parametrize("process", ["api", "migrate"])
def test_a189_check_neither_starts_the_service_nor_opens_connections(
    master_key: str, process: str
) -> None:
    """A189: сервис не запускается и в базу не ходит — команда под стражем аудита Python:
    соединение, разрешение имени, новый процесс или импорт uvicorn, драйвера базы, клиента Redis
    и приложения api — выход 97. Годный бой — код 0 и тот же итог, что без стража."""
    done = run(["-c", GUARDED, process], battle(master_key, process))
    assert done.returncode == 0, done.stdout + done.stderr
    assert "проверено имён:" in done.stdout


@pytest.mark.parametrize("argv", [[], ["web"], ["api", "migrate"]], ids=["none", "web", "two"])
def test_a189_other_arguments_are_the_usage_line(
    command: ModuleType, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    """A189: процесс один из трёх проверки старта — иначе строка использования, код 2."""
    assert command.main(argv) == 2
    assert capsys.readouterr() == ("", command.USAGE + "\n")
    assert command.USAGE == (
        "использование: python -m ai_geo_audit.ops.config_check api|worker|migrate"
    )


def test_a189_processes_are_those_of_the_startup_checks(command: ModuleType) -> None:
    """A189: процессы команды — те же, что у проверки старта: модуль проверки старта грузит
    конфиг, и до `try` команда его не импортирует — список свой, равенство держит тест."""
    assert get_args(sc.Process) == command.PROCESSES


def rebuild(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
    """Конфиг процесса — заново с окружением `env`, как после перезапуска."""
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    domains = {name: getattr(config, name) for name in config.__all__ if name.islower()}
    for name, built in domains.items():
        if isinstance(built, config.Settings):
            monkeypatch.setattr(config, name, type(built)())


@pytest.fixture
def config_not_imported(monkeypatch: pytest.MonkeyPatch) -> None:
    """Пакет конфига ещё не импортирован: импорт в команде исполнит его заново с окружением
    теста; прежний модуль и атрибут пакета вернутся после теста."""
    monkeypatch.delitem(sys.modules, "ai_geo_audit.config")
    monkeypatch.delattr(ai_geo_audit, "config")


def test_a189_in_process_import_refusal_is_the_process_refusal(
    command: ModuleType,
    config_not_imported: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A189, ветка отказа импорта в процессе: негодное значение роняет импорт конфига внутри
    команды — код 1, текст отказа процесса `ai_geo_audit.refusal` целиком, вывод пуст."""
    monkeypatch.setenv("MIGRATION_LOCK_WAIT_SECONDS", "abc")
    with pytest.raises(ValidationError) as refused:
        config.StorageSettings()
    assert command.main(["api"]) == 1
    assert capsys.readouterr() == ("", invalid_settings("api", refused.value) + "\n")


def test_a189_in_process_battle_refusal_names_all_at_once(
    command: ModuleType,
    master_key: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A189, ветка проверки старта в процессе: два негодных имени api — код 1, текст отказа
    старта целиком, вывод пуст."""
    rebuild(monkeypatch, short_and_placeholder(master_key, "api"))
    head = "Процесс «api» не стартует в боевом режиме:"
    assert command.main(["api"]) == 1
    assert capsys.readouterr() == ("", f"{head}\n  {SHORT_DB}\n  {PLACEHOLDER_JWT}\n")


@pytest.mark.parametrize(("mode", "process"), [("prod", "migrate"), ("dev", "api")])
def test_a189_in_process_good_settings_name_mode_and_count(
    command: ModuleType,
    master_key: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode: str,
    process: str,
) -> None:
    """A189, годная ветка в процессе: код 0, строка с процессом, режимом и числом имён; вне боя
    проверка старта молчит — режим в строке это и говорит."""
    rebuild(monkeypatch, battle(master_key, process, APP_ENV=mode))
    names = len(config.env_names())
    assert command.main([process]) == 0
    expected = f"Конфиг процесса «{process}» годен, режим {mode}: проверено имён: {names}\n"
    assert capsys.readouterr() == (expected, "")
