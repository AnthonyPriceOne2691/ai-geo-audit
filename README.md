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

## Миграции

- Схема базы — цепочкой ревизий alembic: `cd backend && uv run alembic upgrade head`; адрес базы —
  `DATABASE_URL`. Тестовая база строится той же командой.
- Миграции — только командой, не из работающего сервиса: `env.py` сам запускает цикл событий,
  и `command.upgrade` из работающего цикла падает с «asyncio.run() cannot be called from a
  running event loop».
- Два `migrate` разом друг другу не мешают: прогон берёт замок миграций, второй ждёт первого не
  дольше `MIGRATION_LOCK_WAIT_SECONDS` (120 с) и выходит с кодом 0. Дольше — отказ «миграции
  уже идут». Ревизия ждёт чужую блокировку таблицы не дольше 10 с — отказ «таблицу держит
  другая транзакция», база остаётся на прежней ревизии.
- Кто держит замок миграций (ключ — `storage/locks.py`, `MIGRATIONS`):
  `SELECT a.pid, a.application_name, a.xact_start FROM pg_locks l JOIN pg_stat_activity a
  USING (pid) WHERE l.locktype = 'advisory' AND l.granted AND l.objid = 1001;`
- Новая ревизия — `uv run alembic revision -m "что меняет" --rev-id 0002`: номер следующий по
  порядку, четыре цифры. Две ветки принесли один номер — перенумеровывает тот, кто сливает вторым;
  повтор и пропуск номера ловит тест цепочки.
- Модели — от `storage.models.Base`: целые — `bigint`, строки — `text`, время — с поясом. Узкий тип,
  плавающая точка, явное время без пояса и `SET NULL` у ключа — только поимённо, с причиной; у
  ключа — явный `ondelete`.
- Статус — одно перечисление `StrEnum` в одном модуле, колонка — через `enum_column`.
- Колонка, добавленная задним числом, получает NULL — «неизвестно», а не умолчание.
