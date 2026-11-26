# ai-geo-audit

Сервис комплексного аудита сайта: техническое SEO, готовность к ИИ-поиску (GEO)
и видимость бренда в ответах ИИ-платформ. Результат — отчёт для человека и
таблица с данными.

**Статус:** каркас в работе, кода сервиса пока нет.

**Стек:** Python 3.12, FastAPI, PostgreSQL 16, SQLAlchemy и Alembic, Redis и RQ,
React с Vite и Mantine, pytest, docker compose.

## Разработка

- База разработки и тестов — `docker compose -f docker-compose.dev.yml up -d db` (127.0.0.1:5462).
- Redis разработки и тестов — `docker compose -f docker-compose.dev.yml up -d redis`
  (127.0.0.1:6462, публичный пароль стенда `audit-dev-redis`): без него тесты не стартуют одной
  строкой. У процесса pytest своя база Redis из 1–15 под меткой со сроком; свой Redis —
  `TEST_REDIS_URL` без номера базы.
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

## Ключи и настройки

- Ключи сервисов (OpenAI, Claude, Gemini, Perplexity, Ahrefs, PageSpeed Insights) — в таблице
  `service_keys` шифротекстом Fernet с номером мастер-ключа, отпечатком и подсказкой; открытого
  значения нет ни в базе, ни в журнале. Ключ берёт только `settings.keys.resolve` — в момент
  вызова, без кэша; модели, шифр и запросы к `service_keys` вне `settings/` не пускает тест A229.
- Обычные настройки — `setting_versions`: правка — новая версия; без строк — умолчание реестра.
- Мастер-ключ — файл `KEYS_MASTER_KEY_FILE`: строки по 64 знака hex (32 случайных байта); первая
  шифрует новые записи, остальные — только для расшифровки; заглавные hex и CRLF принимаются. Без
  годного файла api и worker в бою не стартуют; копию файла держите отдельно от сервера.
- Свой файл разработчика — вне рабочей копии: `openssl rand -hex 32 > <файл> && chmod 600 <файл>`,
  затем `KEYS_MASTER_KEY_FILE=<файл>`. Тесты и оракул стека пишут свой временный файл.
- Разбор конфига без старта — `python -m ai_geo_audit.ops.config_check api` (так же `worker` и
  `migrate`): тот же приговор, что даст старт процесса, — негодные и недостающие в бою имена
  разом, без значений; годный — код 0 и «проверено имён: N». Сервис не запускается, в базу и
  Redis не ходит.

## Справочник проверок

- Редакции справочника — `reference_revisions`, строки и пороги — `reference_checks`,
  `reference_params`: строки и пороги не меняются никогда (триггер `reference_frozen`), правка —
  новая редакция, активна одна. Откат ревизии `0003` после загрузки стирает справочник.
- Файл — JSON формата 1 в UTF-8 без BOM: `format`, `source_date`, `rows`; тексты заказчика в git не
  идут, в git — манифест `reference/manifest.py` (строк, sha256, дата снимка) по файлу ведущей.
- Проверка без записи: `docker compose run --rm -T api python -m ai_geo_audit.reference check -`
  с файлом на стандартный ввод (`-T` обязателен) — все причины за прогон или «было — станет»
  против активной редакции и последней строкой знак записи `<sha256>@<номер>`; база не меняется.
- Загрузка в базу боя — только по слову владельца (Ч2.1б); на сервер ходит ведущая.

## Стек compose и оракул

- Стек — `docker-compose.yml`: db, redis, migrate, api и web. Наружу — только порт web на петле
  машины (`WEB_PORT`), к нему ходит прокси хоста. Переменные стека — блок в `.env.example`;
  подъём — `docker compose up -d --wait`: migrate прогоняет миграции и выходит, api стартует
  только после его успешного выхода, web — когда api healthy. Сбой migrate назван по имени.
- Образ сервиса — `deploy/backend/Dockerfile`, контекст — корень, белый список — рядом
  (`Dockerfile.dockerignore`, нужен BuildKit). Базы — по дайджесту; обновление — правкой тега и
  дайджеста со строкой в decisions поставки.
- Образ web — `deploy/web/Dockerfile`: фронт собирает node 24, отдаёт nginx 1.30 от uid 101;
  в корне сайта — ровно `dist/`, в `conf.d` — только `deploy/web/nginx.conf`. Заголовки защиты —
  сниппет `security-headers.conf`, CSP и кэш — в `nginx.conf`; `/api/` — к api через DNS
  докера, отказы 413, 502 и 504 — образцы раздела `proxy` в `contracts/api-refusals.json`.
- Сети: `data` — db, redis, migrate и api, `front` — api и web, обе без выхода наружу; `edge` —
  обычная, только у web. У web постоянный адрес `WEB_ADDRESS` в подсети `FRONT_SUBNET` — он же
  единственный доверенный прокси api (`API_TRUSTED_PROXIES`).
- Права: у всех `cap_drop: [ALL]`, `no-new-privileges` и корень только для чтения; запись — в
  `tmpfs` и томах; db и redis — под пользователем своего образа.
- Остановка: `stop_grace_period` — у api 30 с (мягкая остановка uvicorn 20 с и 10 с на закрытие
  пулов), у db 60 с. `docker compose stop api` даёт код 143, а не 0: uvicorn 0.54 после мягкой
  остановки снова поднимает SIGTERM; с записью «Finished server process» в журнале это норма.
  Разбор — `ai_geo_audit.ops.stop_verdict`: «остановлен», «убит по сроку» (137) или «упал».
- Правила файла — `python -m ai_geo_audit.ops.stack_rules` над выводом
  `docker compose -f docker-compose.yml --profile '*' config --no-interpolate --format json`:
  код 0 — нарушений нет, 1 — нарушения, 2 — отказ.
- Боевое наложение — `docker-compose.prod.yml`: лимиты памяти и процессора у каждого сервиса,
  журнал journald с меткой сервиса (`ai-geo-audit-api` и так далее), потолок Redis без
  вытеснения, `max_connections` базы; подъём — `docker compose -f docker-compose.yml -f
  docker-compose.prod.yml up -d`. Правила наложения — тот же модуль с флагом `--prod` над слитым
  выводом обоих файлов.
- Оракул — `python3 scripts/stack_oracle.py`: собирает образы сервиса и web из чистой копии
  `git archive HEAD`, поднимает стек под своим именем и убирает его в конце прогона; порт web,
  подсеть `front` и адрес web — свои у прогона, от имени стека. Нужны Docker с compose v2 и
  BuildKit; код 0 — все шаги прошли.
- Уборка оракула — по своему имени `ai-geo-audit-oracle-` и 10 знакам sha256 пути копии: остатки
  прошлого прогона снимаются на старте, по Ctrl-C и SIGTERM уборка идёт ровно одна; второй прогон
  той же копии ждёт замка. В CI оракул — джоба `stack` на запросе и на push в main.
- Сканер образа — `python3 scripts/image_scan.py install --dir D`, затем `run --work W --trivy
  D/trivy` и `sbom --work W --trivy D/trivy`: Trivy 0.76.2 файлом выпуска по sha256, скан файла
  `docker save`. Только на раннере — джоба `image-scan`; после ручного прогона на Linux —
  `docker image rm ai-geo-audit-scan:backend ai-geo-audit-scan:web` и каталог `--work`.

## Роли базы

- Процессы ходят в базу ролями: migrate — `audit_migrate`, владелец базы и таблиц; api и worker —
  `audit_app`: `SELECT`, `INSERT`, `UPDATE`, `DELETE` на таблицы, `USAGE` и `SELECT` на
  последовательности. Суперпользователь `audit` — только у db; чужая роль в адресе процесса в бою
  не даёт старта.
- Роли заводит `deploy/db/roles.sql` суперпользователем образа: на пустом томе — сам образ, на
  поднятом — команда ниже. Пароли — `POSTGRES_MIGRATE_PASSWORD` и `POSTGRES_APP_PASSWORD` из
  окружения db, в файле их нет. Каждый запуск сводит роли к одному виду: лишнее у роли сервиса
  отзывается, нужное выдаётся заново. Команда для поднятого тома — до подъёма migrate и api; её
  позовёт выкат (6в), до тех пор — руками:

  ```
  docker compose exec -T db sh -c \
    'psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
      -f /docker-entrypoint-initdb.d/roles.sql'
  ```

- Стенд, поднятый до ролей: пароли ролей и адреса с ролями — в файл окружения сервера (блок в
  `.env.example`); `docker compose up -d --no-deps db` — db получает пароли и скрипт; команда для
  поднятого тома — таблицы суперпользователя переходят роли миграций; `docker compose up -d
  --wait` — migrate и api с новыми адресами.
- Смена пароля роли — тем же порядком: новый пароль — в файл окружения, в пароль роли и в адрес её
  процесса; `up -d --no-deps db`, команда для поднятого тома, `up -d --wait`. Между командой и
  подъёмом процесс с прежним адресом новых соединений не открывает.
- Проверка — `docker compose exec -T db psql -X -U audit -d audit -c '\du' -c '\l' -c '\dn+' -c
  '\dp' -c '\ddp'`: роли и атрибуты, владелец базы, схема `public`, права и права по умолчанию.
- Снятие ролей — только с откатом сервиса на поставку без ролей: проверка старта требует роль
  процесса. Адреса процессов — снова на `audit`, затем:

  ```
  docker compose exec -T db psql -X -1 -v ON_ERROR_STOP=1 -U audit -d audit \
    -c 'DROP OWNED BY audit_app' -c 'REASSIGN OWNED BY audit_migrate TO audit' \
    -c 'DROP OWNED BY audit_migrate' -c 'DROP ROLE audit_app' -c 'DROP ROLE audit_migrate'
  ```

## Очередь Redis

- Очередь — redis с паролем. `REDIS_PASSWORD` — в окружении контейнера redis; сервер берёт её
  строкой `requirepass` со входа — here-doc команды `sh -c`, а не аргументом: ни в списке
  процессов, ни в команде контейнера (`Config.Cmd` в `docker inspect`), ни в журнале пароля нет; в
  `Config.Env` он есть — как пароли у db. Проверка здоровья — ответ `PONG` при `REDISCLI_AUTH`, а
  не код `redis-cli`: без пароля `redis-cli` выходит с кодом 0. Других сервисов с `REDIS_PASSWORD`
  нет — правило `redis-auth`.
- Адрес очереди api — `REDIS_URL` из переменной сервера `API_REDIS_URL`, вида
  `redis://:<пароль>@redis:6379/0`: пароль тот же, что в `REDIS_PASSWORD`; знаки `@ / ? #` в
  пароле — кодом (`%40`, `%2F`, `%3F`, `%23`). Проверка старта в бою: пароль короче 12 знаков,
  заглушка `CHANGE_ME` и незакодированные знаки — отказ с именем `REDIS_URL`, api не стартует.
- Смена пароля: новый пароль — в `REDIS_PASSWORD` и в `API_REDIS_URL` файла окружения сервера, в
  адресе — закодированным; затем `docker compose up -d --wait`. Redis читает настройки только на
  старте: правка файла без пересоздания контейнера ничего не меняет.
- Стенд, поднятый до пароля: `REDIS_PASSWORD` и `API_REDIS_URL` — в файл окружения сервера (блок в
  `.env.example`): без них compose стек не поднимает — обе переменные обязательны; затем
  `docker compose up -d --wait`.
- Проверка — ответ `PONG`; без пароля — `NOAUTH`:

  ```
  docker compose exec -T redis sh -c 'REDISCLI_AUTH="$REDIS_PASSWORD" redis-cli ping'
  ```

## Фронт

- Node 24 (`frontend/.nvmrc`); пакеты — `cd frontend && npm ci`: только по lock, скрипты
  установки выключены.
- Проверка — `npm run check`: линтер, формат, типы, тесты, сборка и оракул сборки.
- Браузеры — Chrome и Edge 111+, Firefox 121+, Safari 15.4+; почему — в `frontend/UI_RULES.md`.
