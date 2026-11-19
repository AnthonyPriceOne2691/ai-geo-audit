"""Окружение тестов (A31): до первого импорта пакета из окружения уходит всё, что читает сервис
или драйвер. Имена — из `.env.example` (он полон по тесту A8), без импорта конфига: конфиг
собрался бы раньше вычистки. Сравнение без регистра: конфиг читает окружение так же.
`TEST_DATABASE_URL` остаётся — его читает `dbtools`.
"""

from __future__ import annotations

import os
import re
from collections.abc import MutableMapping
from pathlib import Path

ROOT = Path(os.environ.get("AUDIT_REPO_ROOT") or Path(__file__).resolve().parents[2])
EXAMPLE = ROOT / ".env.example"
#: Ключи провайдеров и переменные libpq (`PGSSLMODE=require` ронял бы каждое соединение тестов).
PREFIXES = ("OPENAI_", "ANTHROPIC_", "DATAFORSEO_", "AHREFS_", "GEMINI_", "GOOGLE_", "PERPLEXITY_")
PROXIES = frozenset({"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "ENV_FILE"})
KEEP = frozenset({"TEST_DATABASE_URL"})


def _dirty(name: str, known: frozenset[str]) -> bool:
    upper = name.upper()
    if upper in KEEP:
        return False
    return upper in known or upper in PROXIES or upper.startswith((*PREFIXES, "PG"))


def clean(environ: MutableMapping[str, str] = os.environ) -> list[str]:
    """Убрать переменные сервиса, драйвера и прокси; `ENV_FILE` — `/dev/null`. Убранные имена."""
    known = frozenset(re.findall(r"(?m)^([A-Z][A-Z0-9_]*)=", EXAMPLE.read_text(encoding="utf-8")))
    gone = sorted(name for name in environ if _dirty(name, known))
    for name in gone:
        del environ[name]
    environ["ENV_FILE"] = os.devnull
    return gone
