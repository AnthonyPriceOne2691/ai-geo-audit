"""Каталоги наборов (A338): метрики видимости и проверки выдачи — постоянные в коде.

Метрики — 11 строк листа «AI Search Visibility»; признак «в GEO Score» — у семи из строки «Общий
GEO Score» того же листа (ТЗ:251). Сама строка «Общий GEO Score» — итог, а не код каталога: его
показывают, когда блок целиком; расчёт — Часть 8. Проверки выдачи — 7 строк листа «Экспертиза
выдачи» на ответах ИИ (ТЗ:19), поведение — Часть 7. Описаний, норм и порогов листов здесь нет —
только код, название и признак; порядок — порядок строк листа: в нём `codes_of` отдаёт коды.
"""

from __future__ import annotations

from typing import Final, NamedTuple


class Metric(NamedTuple):
    code: str
    name: str
    in_geo_score: bool


class AnswerCheck(NamedTuple):
    code: str
    name: str


METRICS: Final = (
    Metric("visibility_score", "AI Visibility Score", True),
    Metric("share_of_voice", "Share of Voice", True),
    Metric("brand_mentions", "Brand Mentions", False),
    Metric("prompt_coverage", "Prompt Coverage", True),
    Metric("citation_coverage", "Citation Coverage", True),
    Metric("source_coverage", "Source Coverage", False),
    Metric("competitor_visibility", "Competitor Visibility", False),
    Metric("source_gap", "Source Gap Analysis", True),
    Metric("prompt_gap", "Prompt Gap Analysis", True),
    Metric("historical_dynamics", "Historical Dynamics", True),
    Metric("platform_coverage", "AI Platform Comparison", False),
)
ANSWERS: Final = (
    AnswerCheck("answer_mention", "Упоминание бренда"),
    AnswerCheck("answer_recommendation", "Рекомендация бренда"),
    AnswerCheck("answer_competitors", "Конкурентное преимущество"),
    AnswerCheck("answer_brand_facts", "Качество информации о бренде"),
    AnswerCheck("answer_as_source", "Использование как источник"),
    AnswerCheck("answer_recommendation_quality", "Качество рекомендаций"),
    AnswerCheck("answer_query_types", "Видимость по типам запросов"),
)
