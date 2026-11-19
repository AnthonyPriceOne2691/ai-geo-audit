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

- Тесты ходят только на петлю и не видят окружения машины: `.env` в рабочие копии ссылкой не
  подключается — у каждой копии своё окружение и своя тестовая база.

## Гейты качества кода

- Гейты формы — канон CQG `cqg@3.14`, половина Python; фронтовые половины — следующей поставкой.
  Хуки — `.pre-commit-config.yaml`, скрипты гейтов — `scripts/lint/`, настройки ruff, mypy,
  pytest и покрытия — `backend/pyproject.toml`, слои — `backend/.importlinter`.
- Инструменты — dev-группа uv: `cd backend && uv sync --frozen`. Версия uv — 0.11.26, другая отказывает.
- Каталог хуков git у клона один на все рабочие копии: в нём заглушки `scripts/hooks/stub.sh`,
  ставит их `bash scripts/hooks/install-stubs.sh` — по слову проверяющего. Заглушка зовёт
  `scripts/hooks/run-hooks.sh` той копии, где идёт коммит или push, а он — pre-commit из её
  `backend/.venv`. `pre-commit install` не годится: он пишет в хук путь к Python одной копии.
- Хуки руками, из своей копии: `uv run --project backend pre-commit run --all-files`, стадия
  pre-push — с `--hook-stage pre-push`. Все ли гейты подключены —
  `bash scripts/lint/check_gate_coverage.sh`; судят ли они — `python3 scripts/lint/contour_doctor.py`.
- Аудит зависимостей Python ходит в сеть: у его хука `stages: [manual]`, место — CI; руками —
  `LINT_FE_DIR=.frontend-not-here bash scripts/lint/check_deps_audit.sh`.
- Гейты видят только файлы в индексе: новый файл — `git add <файл>`, потом прогон.

## Сервер API

- Запуск — `cd backend && uv run python -m ai_geo_audit.api`: адрес и порт — `API_HOST` и
  `API_PORT` (по умолчанию 127.0.0.1:8107), uvicorn получает все настройки явно и своё окружение
  не читает. Негодная настройка или недостающая в бою — отказ словами до сервера, без трассировки.
- `SIGTERM` — мягкая остановка: запросы в работе доживают до 20 с, процесс кончается тем же
  сигналом.
- Адреса прокси, которым сервер верит адрес клиента, — `API_TRUSTED_PROXIES`: адреса и сети через
  запятую, не шире /16 у IPv4 и /64 у IPv6; `*` нельзя. В бою задаётся явно — адрес web.

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

## Фронт

- Node 24 (`frontend/.nvmrc`); пакеты — `cd frontend && npm ci`: только по lock, скрипты
  установки выключены.
- Проверка — `npm run check`: линтер, формат, типы, тесты, сборка и оракул сборки.
- Браузеры — Chrome и Edge 111+, Firefox 121+, Safari 15.4+; почему — в `frontend/UI_RULES.md`.
