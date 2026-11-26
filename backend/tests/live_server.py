"""Живой сервер подпроцессом — живой тест A55 и smoke S1: свободный порт и окружение
`python -m ai_geo_audit.api` — целиком, а не «копия плюс добавки» (уроки Ч1.2в, L6, L12)."""

from __future__ import annotations

import os
import socket
from pathlib import Path

import redistools


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def live_env(port: int, url: str, src: Path) -> dict[str, str]:
    """Окружение сервера: адрес базы `url`, Redis теста (A243), пакет — из каталога `src`, порт на
    петле, журнал —
    JSON; `.env` не читается, переменные разработчика не доходят, `COVERAGE_*` — проходят."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONUTF8": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(src),
        "ENV_FILE": os.devnull,
        "DATABASE_URL": url,
        "REDIS_URL": redistools.current_url(),
        "APP_ENV": "dev",
        "API_HOST": "127.0.0.1",
        "API_PORT": str(port),
        "LOG_FORMAT": "json",
    }
    return env | {k: v for k, v in os.environ.items() if k.startswith("COVERAGE_")}
