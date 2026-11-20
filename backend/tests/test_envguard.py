"""Окружение машины до тестов не доходит (A31): подпроцесс pytest с грязным окружением."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import dbtools
import envguard
import pytest

PROBE = dbtools.BACKEND / "tests" / "env_probe" / "probe_env.py"
PYTEST = [sys.executable, "-m", "pytest", "-q", "-s", "-p", "no:cacheprovider", "-W", "error"]


def test_a31_dotenv_in_the_working_directory_does_not_reach_the_tests(tmp_path: Path) -> None:
    """A31: в рабочем каталоге запуска лежит `.env` с `APP_ENV=prod`, адресом базы с паролем-меткой
    и ключом провайдера, `ENV_FILE` в окружении нет — внутри `dev`, адрес базы по умолчанию, меток
    в выводе нет: `ENV_FILE` ставится в `/dev/null`, и умолчание `.env` не читается. Держит порчу
    «строка `ENV_FILE=/dev/null` убрана» (порча N6 проверяющего); настоящий `backend/` не
    трогается."""
    (tmp_path / ".env").write_text(
        "APP_ENV=prod\n"
        # pragma: allowlist nextline secret
        "DATABASE_URL=postgresql+asyncpg://audit:dotenv-pass-5150@db:5432/audit\n"
        # pragma: allowlist nextline secret
        "OPENAI_API_KEY=sk-dotenv-mark-5150\n",
        encoding="utf-8",
    )
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONUTF8": "1",
        "TEST_DATABASE_URL": dbtools.server_url().render_as_string(hide_password=False),
    }
    run = subprocess.run(
        [*PYTEST, str(PROBE)],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    out = run.stdout + run.stderr
    assert run.returncode == 0, out[-1500:]
    found = re.search(r"A31-PROBE (\{.*\})", run.stdout)
    assert found, out[-1500:]
    seen = json.loads(found.group(1))
    assert (seen["env"], seen["default_db"]) == ("dev", True)
    assert "dotenv-pass-5150" not in out and "sk-dotenv-mark-5150" not in out


def test_a31_test_database_url_stays_even_in_the_sample_names() -> None:
    """A31: имя `TEST_DATABASE_URL` попало в список имён образца окружения — вычистка его
    оставляет: его читает `dbtools`, без него тесты молча ушли бы на сервер по умолчанию; соседнее
    имя из того же списка уходит. Держит порчу «`KEEP` пуст» (порча N1 проверяющего)."""
    known = frozenset({"TEST_DATABASE_URL", "APP_ENV"})
    assert envguard._dirty("TEST_DATABASE_URL", known) is False
    assert envguard._dirty("APP_ENV", known) is True


#: Имя — уходит ли оно без списка образца: по имени на каждую из семи приставок провайдеров, одно
#: `PG…`, четыре прокси, `ENV_FILE` и строчная запись; обратная сторона — имена процесса.
LISTED = [
    ("OPENAI_API_KEY", True),
    ("ANTHROPIC_API_KEY", True),
    ("DATAFORSEO_LOGIN", True),
    ("AHREFS_API_KEY", True),
    ("GEMINI_API_KEY", True),
    ("GOOGLE_API_KEY", True),
    ("PERPLEXITY_API_KEY", True),
    ("PGPASSWORD", True),
    ("HTTP_PROXY", True),
    ("HTTPS_PROXY", True),
    ("ALL_PROXY", True),
    ("NO_PROXY", True),
    ("ENV_FILE", True),
    ("http_proxy", True),
    ("PATH", False),
    ("HOME", False),
    ("PYTHONUTF8", False),
]


@pytest.mark.parametrize(("name", "gone"), LISTED)
def test_a31_every_listed_name_is_cleaned_and_process_names_stay(name: str, gone: bool) -> None:
    """A31: без списка образца уходит каждое имя, названное в In scope: по имени на каждую из семи
    приставок провайдеров, `PGPASSWORD`, четыре прокси, `ENV_FILE` и строчная `http_proxy`;
    `PATH`, `HOME` и `PYTHONUTF8` остаются. Держит порчи N8–N11 проверяющего: убраны приставки
    `PERPLEXITY_` и `DATAFORSEO_`, прокси `ALL_PROXY` и `HTTP_PROXY`."""
    assert envguard._dirty(name, frozenset()) is gone


def test_a31_dirty_environment_does_not_reach_the_tests(tmp_path: Path) -> None:
    """A31: в окружении запуска `APP_ENV=prod` и `app_env=prod` строчными, `ENV_FILE` с
    `APP_ENV=prod`, адрес базы с паролем, ключи провайдеров, `LOG_FORMAT=json`, `PGSSLMODE`,
    прокси и `TEST_DATABASE_URL` — внутри `dev`, адрес базы по умолчанию, остаётся только
    `TEST_DATABASE_URL`; проба соединяется с базой, хотя `PGSSLMODE=require`. В выводе — число
    вычищенных имён, без значений."""
    prod_env = tmp_path / "prod.env"
    prod_env.write_text("APP_ENV=prod\n", encoding="utf-8")
    dirty = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONUTF8": "1",
        "APP_ENV": "prod",
        "app_env": "prod",
        "ENV_FILE": str(prod_env),
        # pragma: allowlist nextline secret
        "DATABASE_URL": "postgresql+asyncpg://audit:env-pass-123456@db:5432/audit",
        # pragma: allowlist nextline secret
        "OPENAI_API_KEY": "sk-test-not-a-real-key-1",
        # pragma: allowlist nextline secret
        "ANTHROPIC_API_KEY": "ak-test-not-a-real-key-1",
        # pragma: allowlist nextline secret
        "GEMINI_API_KEY": "gm-test-not-a-real-key-1",
        "LOG_FORMAT": "json",
        "PGSSLMODE": "require",
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "TEST_DATABASE_URL": dbtools.server_url().render_as_string(hide_password=False),
    }
    run = subprocess.run(
        [*PYTEST, "tests/env_probe/probe_env.py"],
        cwd=dbtools.BACKEND,
        env=dirty,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert run.returncode == 0, run.stdout[-1500:] + run.stderr[-1500:]
    found = re.search(r"A31-PROBE (\{.*\})", run.stdout)
    assert found, run.stdout[-1500:]
    seen = json.loads(found.group(1))
    assert seen == {"env": "dev", "default_db": True, "left": ["TEST_DATABASE_URL"]} | {
        "cleaned": seen["cleaned"]
    }
    assert seen["cleaned"] >= 10
    assert "env-pass-123456" not in run.stdout + run.stderr
