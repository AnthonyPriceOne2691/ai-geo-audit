# ai-geo-audit

Сервис комплексного аудита сайта: техническое SEO, готовность к ИИ-поиску (GEO)
и видимость бренда в ответах ИИ-платформ. Результат — отчёт для человека и
таблица с данными.

**Статус:** каркас в работе, кода сервиса пока нет.

**Стек:** Python 3.12, FastAPI, PostgreSQL 16, SQLAlchemy и Alembic, Redis и RQ,
React с Vite и Mantine, pytest, docker compose.

## Разработка

- База разработки и тестов — `docker compose -f docker-compose.dev.yml up -d db` (127.0.0.1:5462).
- Тесты — `cd backend && uv run pytest -q`; база копии — `audit_test_` + 10 знаков sha256 пути
  корня. Снятую копию — `docker compose -f docker-compose.dev.yml exec db dropdb -U audit <база>`.
