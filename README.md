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

- Гейты формы — канон CQG `cqg@3.30`, половина Python; фронтовые половины — следующей поставкой.
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

## CI на GitHub

- `.github/workflows/quality.yml` — джобы `gates` (pre-commit и гейты), `delivery` (фазовый гейт,
  подпись необратимого) и `tests` (служба Postgres по дайджесту, сверка замка, набор с покрытием,
  покрытие изменённого, мутации — только на запросе); `main-guard.yml` — задача «RED on main» на
  красный прогон quality после push в main. Правила держат `backend/tests/test_ci_contract.py` и
  `backend/tests/test_tests_job.py`.
- Будущая джоба образа или выката идёт после проверок: `needs: [gates, delivery, tests]`.
- Гейт мержа — `scripts/merge_guard.sh`: слитое с main дерево (pre-commit, ратчет снимков, гейт
  поставки) и последним — статус CI ветки. Из рабочей копии поставки — только проверка:
  `uv run --project backend --no-sync env DRY_RUN=1 bash scripts/merge_guard.sh <ветка>` (pre-commit
  гейт ищет в PATH). Сливает проверяющий из основного клона.

### Настройки репозитория

Выполняет проверяющий по слову владельца, по команде на настройку; `{owner}/{repo}` `gh`
подставляет сам. Проверка — те же пути запросом `GET`.
- Защита main — после первого зелёного прогона ветки: до него GitHub не знает имён проверок.
  ```bash
  gh api -X PUT repos/{owner}/{repo}/branches/main/protection --input - <<'JSON'
  {"required_status_checks": {"strict": true, "contexts": ["gates", "delivery", "tests"]},
   "enforce_admins": true, "required_pull_request_reviews": null, "restrictions": null,
   "allow_force_pushes": false, "allow_deletions": false}
  JSON
  ```
- Токен прогона по умолчанию — только чтение, Actions не одобряют запросы:
  `gh api -X PUT repos/{owner}/{repo}/actions/permissions/workflow -f default_workflow_permissions=read -F can_approve_pull_request_reviews=false`
- Прогон из чужих копий репозитория — только после одобрения:
  `gh api -X PUT repos/{owner}/{repo}/actions/permissions/fork-pr-contributor-approval -f approval_policy=all_external_contributors`
- Разрешённые действия — только GitHub:
  `gh api -X PUT repos/{owner}/{repo}/actions/permissions -F enabled=true -f allowed_actions=selected`,
  затем `gh api -X PUT repos/{owner}/{repo}/actions/permissions/selected-actions -F github_owned_allowed=true -F verified_allowed=false`

## Сервер API

- Запуск — `cd backend && uv run python -m ai_geo_audit.api`: адрес и порт — `API_HOST` и
  `API_PORT` (по умолчанию 127.0.0.1:8107), uvicorn получает все настройки явно и своё окружение
  не читает. Негодная настройка или недостающая в бою — отказ словами до сервера, без трассировки.
- `SIGTERM` — мягкая остановка: запросы в работе доживают до 20 с, движок базы закрывается,
  процесс кончается тем же сигналом.
- Адреса прокси, которым сервер верит адрес клиента, — `API_TRUSTED_PROXIES`: адреса и сети через
  запятую, не шире /16 у IPv4 и /64 у IPv6; `*` нельзя. В бою задаётся явно — адрес web.
- Пределы входа — в коде (`backend/src/ai_geo_audit/api/limits.py`), не настройкой: тело запроса
  — не больше 1 МБ, сверх — 413 `too-large`; запросов в работе — не больше 32 на процесс, сверх —
  503 `busy` с `Retry-After: 60`, и у здоровья тоже. Предел тела равен `client_max_body_size`
  прокси web и стоит в тексте отказа: меняются вместе.
- Схема API и консоли — в `dev` и `test` без пропуска: `/api/openapi.json`, `/api/docs`,
  `/api/redoc`; в бою их нет. На каждом ответе — `Cache-Control: no-store` и `nosniff`.

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

## Стек compose и оракул

- Стек — `docker-compose.yml`: db, redis, migrate и api, портов наружу нет. Переменные стека —
  блок в `.env.example`; подъём — `docker compose up -d --wait`: migrate прогоняет миграции и
  выходит, api стартует только после его успешного выхода. Сбой migrate назван по имени.
- Образ сервиса — `deploy/backend/Dockerfile`, контекст — корень, белый список — рядом
  (`Dockerfile.dockerignore`, нужен BuildKit). Базы — по дайджесту; обновление — правкой тега и
  дайджеста со строкой в decisions поставки.
- Правила файла — `python -m ai_geo_audit.ops.stack_rules` над выводом
  `docker compose -f docker-compose.yml --profile '*' config --no-interpolate --format json`:
  код 0 — нарушений нет, 1 — нарушения, 2 — отказ.
- Оракул — `python3 scripts/stack_oracle.py`: собирает образ из чистой копии `git archive HEAD`,
  поднимает стек под своим именем и убирает его в конце прогона. Нужны Docker с compose v2 и
  BuildKit; код 0 — все шаги прошли.

## Фронт

- Node 24 (`frontend/.nvmrc`); пакеты — `cd frontend && npm ci`: только по lock, скрипты
  установки выключены.
- Проверка — `npm run check`: линтер, формат, типы, тесты, сборка и оракул сборки.
- Браузеры — Chrome и Edge 111+, Firefox 121+, Safari 15.4+; почему — в `frontend/UI_RULES.md`.
