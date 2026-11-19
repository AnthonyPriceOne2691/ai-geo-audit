"""Вход API, срез 1 (A32–A35): настройки входа, сборка приложения, настройки uvicorn, его
журналы и пакеты без лишнего; отказ входа процесса при негодной настройке — словами."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import logging
import os
import subprocess
import sys
import tomllib
from types import ModuleType

import dbtools
import pytest
import uvicorn
from pydantic import ValidationError

from ai_geo_audit import config
from ai_geo_audit.config import startup_checks as sc
from ai_geo_audit.logs import setup as log_setup

#: Домены, которые проверка старта `api` читает из модуля `config`.
DOMAINS = {
    "app": config.AppSettings,
    "storage": config.StorageSettings,
    "access": config.AccessSettings,
    "keys": config.KeySettings,
}
FIVE = ("DATABASE_URL", "REDIS_URL", "JWT_SECRET", "KEYS_MASTER_KEY_FILE", "API_TRUSTED_PROXIES")
STAR = "`*` нельзя: назовите адреса прокси"
WIDE = "сеть шире /16 для IPv4 и шире /64 для IPv6 нельзя"
NOT_ADDRESS = "не адрес и не сеть"
EMPTY = "пустой элемент"


@pytest.fixture
def app_module() -> ModuleType:
    """Модуль приложения — при вызове: до кода среза тест красный, а не сбор тестов."""
    from ai_geo_audit.api import app

    return app


def fresh_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Домены конфига — заново из окружения теста, как у процесса на старте."""
    for name, domain in DOMAINS.items():
        monkeypatch.setattr(config, name, domain())
    monkeypatch.setattr(config, "api", config.ApiSettings())


def test_a32_prod_without_settings_names_all_five_at_once(
    monkeypatch: pytest.MonkeyPatch, app_module: ModuleType
) -> None:
    """A32: `APP_ENV=prod`, ни одной настройки — `create_app()` падает `ConfigError`, в тексте все
    пять имён разом."""
    monkeypatch.setenv("APP_ENV", "prod")
    fresh_config(monkeypatch)
    with pytest.raises(sc.ConfigError) as refusal:
        app_module.create_app()
    assert [name for name in FIVE if name not in str(refusal.value)] == []


def test_a32_dev_without_settings_builds(
    monkeypatch: pytest.MonkeyPatch, app_module: ModuleType
) -> None:
    """A32: в `dev` без единой настройки приложение собирается."""
    fresh_config(monkeypatch)
    app = app_module.create_app()
    assert callable(app)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("10.9.9.9, 172.20.0.0/16", ["10.9.9.9", "172.20.0.0/16"]), ("fd00::/64", ["fd00::/64"])],
)
def test_a33_trusted_proxies_are_a_list_and_reach_uvicorn(
    monkeypatch: pytest.MonkeyPatch, app_module: ModuleType, raw: str, expected: list[str]
) -> None:
    """A33: `API_TRUSTED_PROXIES` — адреса и сети через запятую; конфиг отдаёт список, тот же
    список стоит в `forwarded_allow_ips` настроек uvicorn."""
    monkeypatch.setenv("API_TRUSTED_PROXIES", raw)
    fresh_config(monkeypatch)
    assert config.api.trusted_proxies == expected
    assert app_module.server_settings()["forwarded_allow_ips"] == expected


def test_a33_without_the_variable_only_loopback(app_module: ModuleType) -> None:
    """A33: без переменной — `["127.0.0.1"]`, и он же у uvicorn."""
    assert config.ApiSettings().trusted_proxies == ["127.0.0.1"]
    assert app_module.server_settings()["forwarded_allow_ips"] == ["127.0.0.1"]


REFUSED = [
    ("*", STAR, "*"),
    ("10.9.9.9, *", STAR, "*"),
    ("0.0.0.0/0", WIDE, "0.0.0.0/0"),
    ("0.0.0.0/1", WIDE, "0.0.0.0/1"),
    ("10.0.0.0/8", WIDE, "10.0.0.0/8"),
    ("::/0", WIDE, "::/0"),
    ("fd00::/48", WIDE, "fd00::/48"),
    ("not-an-address", NOT_ADDRESS, "not-an-address"),
    ("", EMPTY, ""),
]


@pytest.mark.parametrize("env", ["dev", "prod"])
@pytest.mark.parametrize(("raw", "why", "element"), REFUSED)
def test_a33_wide_or_bad_proxies_break_the_config_in_any_mode(
    monkeypatch: pytest.MonkeyPatch, env: str, raw: str, why: str, element: str
) -> None:
    """A33: `*`, сеть шире /16 у IPv4 и шире /64 у IPv6, не адрес, пустая строка — конфиг не
    собирается в любом режиме. Ошибка называет `API_TRUSTED_PROXIES` и элемент в тексте своей
    проверки — без введённого значения pydantic (проба 6)."""
    monkeypatch.setenv("APP_ENV", env)
    monkeypatch.setenv("API_TRUSTED_PROXIES", raw)
    with pytest.raises(ValidationError) as refusal:
        config.ApiSettings()
    [error] = refusal.value.errors(include_url=False, include_input=False)
    assert error["loc"] == ("API_TRUSTED_PROXIES",)
    assert why in error["msg"]
    assert element in error["msg"]


def entry(extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """`python -m ai_geo_audit.api` подпроцессом с окружением с нуля: без `.env`, без
    переменных разработчика; `PYTHONPATH` и `COVERAGE_*` проходят — порчам и покрытию."""
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONUTF8": "1", "ENV_FILE": os.devnull}
    env.update({k: v for k, v in os.environ.items() if k == "PYTHONPATH" or "COVERAGE_" in k})
    return subprocess.run(
        [sys.executable, "-m", "ai_geo_audit.api"],
        cwd=dbtools.BACKEND,
        env=env | extra,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_a33_api_entry_refuses_a_bad_setting_in_words() -> None:
    """A33: негодное значение роняет сборку конфига у процесса `api` — `python -m
    ai_geo_audit.api` с `API_TRUSTED_PROXIES=10.9.9.9, *`: код не 0, имя переменной и причина
    словами, без трассировки и без введённого значения — элемент назван только текстом своей
    проверки. Строже показанного под A33 — слово проверяющего 2026-11-19T11:08Z."""
    run = entry({"API_TRUSTED_PROXIES": "10.9.9.9, *"})
    out = run.stdout + run.stderr
    assert run.returncode != 0, out[-1500:]
    assert f"Процесс «api» не стартует, негодные настройки:\n  API_TRUSTED_PROXIES — {STAR}" in out
    assert "Traceback" not in out, out[-1500:]
    assert "10.9.9.9" not in out and "input_value" not in out, out[-1500:]


def test_a33_migrate_refuses_the_same_setting_with_the_same_text() -> None:
    """A33: та же негодная настройка роняет и `migrate` — тем же текстом причины, что у `api`:
    текст строит одна функция, тексты не расходятся."""
    run = dbtools.alembic("upgrade", "head", extra={"API_TRUSTED_PROXIES": "10.9.9.9, *"})
    out = run.stdout + run.stderr
    assert run.returncode != 0, out[-1500:]
    assert (
        f"Процесс «migrate» не стартует, негодные настройки:\n  API_TRUSTED_PROXIES — {STAR}" in out
    )
    assert "Traceback" not in out and "10.9.9.9" not in out, out[-1500:]


def test_a32_api_entry_in_prod_without_settings_refuses_in_words() -> None:
    """A32: `python -m ai_geo_audit.api` с `APP_ENV=prod` и без настроек — код не 0, все пять
    имён, без трассировки: проверка старта до uvicorn, как у `migrate`. Строже показанного."""
    run = entry({"APP_ENV": "prod"})
    out = run.stdout + run.stderr
    assert run.returncode != 0, out[-1500:]
    assert "Процесс «api» не стартует в боевом режиме:" in out, out[-1500:]
    assert [name for name in FIVE if name not in out] == [], out[-1500:]
    assert "Traceback" not in out, out[-1500:]


EXPECTED = {
    "app": "ai_geo_audit.api.app:create_app",
    "factory": True,
    "host": "127.0.0.1",
    "port": 8107,
    "loop": "asyncio",
    "http": "h11",
    "ws": "none",
    "lifespan": "on",
    "log_config": None,
    "access_log": False,
    "proxy_headers": True,
    "forwarded_allow_ips": ["127.0.0.1"],
    "server_header": False,
    "timeout_keep_alive": 5,
    "timeout_graceful_shutdown": 20,
    "workers": 1,
}


def test_a34_server_settings_are_exactly_these(app_module: ModuleType) -> None:
    """A34: `server_settings()` без `API_HOST` и `API_PORT` — ровно эти поля; лишнее поле или
    другое значение — тест красный и печатает расхождение."""
    got = app_module.server_settings()
    differ = {k: (got.get(k), EXPECTED.get(k)) for k in got.keys() | EXPECTED.keys()}
    assert got == EXPECTED, {k: v for k, v in differ.items() if v[0] != v[1]}


def test_a34_host_and_port_come_from_their_variables(
    monkeypatch: pytest.MonkeyPatch, app_module: ModuleType
) -> None:
    """A34: с `API_HOST` и `API_PORT` — их значения."""
    monkeypatch.setenv("API_HOST", "0.0.0.0")
    monkeypatch.setenv("API_PORT", "8200")
    fresh_config(monkeypatch)
    got = app_module.server_settings()
    assert (got["host"], got["port"]) == ("0.0.0.0", 8200)


def test_a34_uvicorn_does_not_read_its_own_env_names(
    monkeypatch: pytest.MonkeyPatch, app_module: ModuleType
) -> None:
    """A34: в окружении `FORWARDED_ALLOW_IPS=*` и `WEB_CONCURRENCY=4` — у собранного
    `uvicorn.Config` наш список и один процесс."""
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")
    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    built = uvicorn.Config(**app_module.server_settings())
    assert (built.forwarded_allow_ips, built.workers) == (["127.0.0.1"], 1)


@pytest.mark.parametrize(("access_log", "has_handlers"), [(False, False), (True, True)])
def test_a34_access_line_does_not_reach_our_handler(
    monkeypatch: pytest.MonkeyPatch, app_module: ModuleType, access_log: bool, has_handlers: bool
) -> None:
    """A34: после `setup_logging()` и сборки `uvicorn.Config(**server_settings())` у логгера
    `uvicorn.access` обработчиков нет — по этому признаку uvicorn решает, писать ли строку
    доступа. При `access_log=True` признак — `True`: строка ушла бы в наш обработчик."""
    access = logging.getLogger("uvicorn.access")
    monkeypatch.setattr(access, "handlers", [])
    monkeypatch.setattr(access, "propagate", True)
    log_setup.setup_logging()
    uvicorn.Config(**app_module.server_settings() | {"access_log": access_log})
    assert access.hasHandlers() is has_handlers


def test_a34_color_message_of_uvicorn_does_not_reach_the_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A34: `setup_logging(fmt="json")`, запись `uvicorn.error` с `color_message`, как у стартовых
    записей uvicorn, — строка JSON без поля `color_message` и без управляющих знаков."""
    log_setup.setup_logging(fmt="json")
    colored = "\x1b[32mstarted\x1b[0m"
    logging.getLogger("uvicorn.error").info("started", extra={"color_message": colored})
    line = capsys.readouterr().err.strip().splitlines()[-1]
    assert "color_message" not in json.loads(line)
    assert "\x1b" not in line and "u001b" not in line


BANNED = ("uvloop", "httptools", "websockets", "watchfiles")


def test_a35_no_extra_server_packages_in_the_lock_or_the_env() -> None:
    """A35: пакетов `uvloop`, `httptools`, `websockets` и `watchfiles` нет ни в `uv.lock`, ни в
    окружении тестов."""
    lock = tomllib.loads((dbtools.BACKEND / "uv.lock").read_text(encoding="utf-8"))
    assert sorted({p["name"] for p in lock["package"]} & set(BANNED)) == []
    assert [name for name in BANNED if importlib.util.find_spec(name) is not None] == []


async def test_a35_test_loop_is_plain_asyncio() -> None:
    """A35: цикл событий теста — класс из модуля `asyncio`."""
    assert type(asyncio.get_running_loop()).__module__.split(".")[0] == "asyncio"
