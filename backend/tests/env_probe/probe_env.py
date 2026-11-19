"""Проба A31: что видит тест в грязном окружении. Имя не `test_*`: в обычный прогон не входит,
её запускает подпроцессом `test_envguard.py`."""

from __future__ import annotations

import json
import os

import conftest

from ai_geo_audit import config

DIRTY = ("APP_ENV", "app_env", "DATABASE_URL", "OPENAI_API_KEY", "GEMINI_API_KEY", "LOG_FORMAT")
DIRTY += ("PGSSLMODE", "HTTPS_PROXY", "TEST_DATABASE_URL")


def test_probe() -> None:
    seen = {
        "env": config.app.env,
        "default_db": "@localhost:5462/" in config.storage.database_url.get_secret_value(),
        "left": sorted(name for name in DIRTY if name in os.environ),
        "cleaned": len(conftest.CLEANED),
    }
    print("A31-PROBE " + json.dumps(seen))
