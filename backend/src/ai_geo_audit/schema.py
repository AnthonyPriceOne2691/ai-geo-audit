"""Один список таблиц сервиса: модули моделей импортируются здесь, и их таблицы ложатся в общий
`models.Base.metadata`. Новая таблица — строка здесь.

Метаданные берут отсюда `migrations/env.py`, правила моделей A15 и сверка перечислений A16 на
моделях сервиса, сверка моделей с базой и S3 smoke: без этого импорта метаданные пусты, и сверки
судили бы пустоту (L121). Импорт модуля моделей без имён — поимённое исключение правила
«ключ — только резолвером» для одного этого файла (A229).
"""

from __future__ import annotations

import ai_geo_audit.reference.models  # таблицы справочника в общих метаданных
import ai_geo_audit.sets.models  # таблицы наборов в общих метаданных
import ai_geo_audit.settings.models  # noqa: F401 -- таблицы ключей и настроек в общих метаданных
from ai_geo_audit.storage import models

metadata = models.Base.metadata
