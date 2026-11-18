# ai-geo-audit

Сервис комплексного аудита сайта: техническое SEO, готовность к ИИ-поиску (GEO)
и видимость бренда в ответах ИИ-платформ. Результат — отчёт для человека и
таблица с данными.

**Статус:** каркас в работе, кода сервиса пока нет.

**Стек:** Python 3.12, FastAPI, PostgreSQL 16, SQLAlchemy и Alembic, Redis и RQ,
React с Vite и Mantine, pytest, docker compose.

## Разработка

- База разработки и тестов: `docker compose -f docker-compose.dev.yml up -d db` (Postgres 16 на
  `127.0.0.1:5462`; стек назван явно, команда из любой копии поднимает тот же контейнер).
- Тесты: `cd backend && uv run pytest -q`. У каждой рабочей копии своя тестовая база
  `audit_test_<10 знаков sha256 пути корня>`; адрес сервера — `TEST_DATABASE_URL`.
- После снятия рабочей копии её тестовую базу сносят из корня этой копии, пока путь ещё тот же:
  `docker compose -f docker-compose.dev.yml exec db dropdb -U audit "audit_test_$(python3 -c 'import hashlib,pathlib;print(hashlib.sha256(str(pathlib.Path.cwd().resolve()).encode()).hexdigest()[:10])')"`
