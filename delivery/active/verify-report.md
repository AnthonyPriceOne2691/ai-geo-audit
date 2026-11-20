# Verify report

**Date:** 2026-11-20
**Verifier:** agent:lead-session
**Builder evidence:** agent:hands-session — прогоны ниже; вердикт, ревью рисковых мест и подпись
под утверждениями — за проверяющим.

**CI run:** n/a reason=CI не развёрнут, волна В1; вместо ссылки — прогон в чистом клоне ниже
**Commit:** `7d599b3` — вершина кода; см. «Прогон в чистом клоне»

## Прогон в чистом клоне

Клон свежий, с ветки `ch1-05a-image-compose` на `7d599b3`; база диффа — `origin/main` клона,
`a8e4597` (main с Ч1.3а, L2). Гейт и pytest — 2026-11-20T10:10:38Z–10:11:28Z, нагрузка 4,14;
оракул из того же клона — 10:11:39Z–10:12:47Z, нагрузка 4,96. Вывод дословный, длинные строки
обрезаны знаком «…»:

```
$ git log --format="%h %p %s" -2 && git rev-parse --short origin/main && git merge-base HEAD origin/main
7d599b3 3cf0233 fix(scripts): Ч1.5а — S603 гейтов В1а: поштучный noqa с причиной у двух вызовов subprocess
3cf0233 eeda8b1 refactor(scripts): Ч1.5а — разрез оракула под file-length В1а: шаги compose — stack_oracle_compose.py
a8e4597
a8e4597b8e431a08d844d5c580aec00aec40e4eb
$ python3 scripts/delivery_check.py --diff-base origin/main; echo "exit=$?"
breakers: files=16 net_loc=2313 (+2314/-1), excluded=8 (delivery/|knowledge/|scripts/lint/|scripts/merge_guard.sh|scripts/delivery_|scripts/okf_|.claude/|docs/…
наблюдение §13.1: поставок в архиве 10, окон со сроком 10, истекших без observed.md 0
WARNING: acceptance-примеры без ссылки в тестах/eval-smoke: A64, A66 (§3.1d) — пометь тест id примера в комментарии или в начале докстринга; строка, целиком ра…
WARNING: class M: ни одного реляционного оракула с генератором входов (§6.5) — в тестах не найдено: `@given` (hypothesis); `fc.property` (fast-check). Отношени…
WARNING: ci-oracles: weak — local gates are bypassable (§10.4); Verifier must attach a clean-clone run to verify-report.md
delivery_check: 0 error(s), 3 warning(s)
exit=0
$ cd backend && uv sync --frozen -q; echo "uv sync exit=$?"
uv sync exit=0
$ uv run pytest -q -p no:cacheprovider -W error | tail -1
430 passed in 42.97s
pytest exit=0
$ python3 scripts/stack_oracle.py; echo "exit=$?"
2026-11-20T10:11:39Z оракул: стек ai-geo-audit-oracle-eacbbb5898, Docker 29.9.3
2026-11-20T10:11:39Z A60 A64 сборка из чистой копии — ок: образ ai-geo-audit-oracle-eacbbb5898-backend:oracle
2026-11-20T10:11:40Z A60 Python и инструменты — ок: Python 3.12.16; requires-python и uv.lock — 3.12; uv, pytest, uvloop — ModuleNotFoundError, uv в PATH нет; …
2026-11-20T10:11:40Z A61 проба контекста — ок: в контексте 31 файлов под пятью открытыми путями, канареек и метки нет
2026-11-20T10:11:42Z A61 A62 метка и .env* в файлах образа — ок: просмотрено файлов 6397: метки и .env* нет
2026-11-20T10:11:45Z A62 файлы пакета и рабочего каталога — ок: пакет — 25 файлов, как в копии; рабочий каталог — alembic.ini и 3 файлов migrations/
2026-11-20T10:11:45Z A63 пользователь образа — ок: Config.User — 10001
2026-11-20T10:11:45Z A64 секреты сборки — ок: значений 5 имён нет в истории и Config.Env
2026-11-20T10:11:45Z A73 правила на настоящем docker-compose.yml — ок: итог: сервисов 4, правил 16, нарушений 0; config --services — 4
2026-11-20T10:11:46Z A65 A63 подъём стека и migrate — ок: migrate — код 0 до старта api; alembic current = heads ['0001']; uid 10001
2026-11-20T10:12:00Z A69 здоровье api после старта — ок: healthy через 5.4 с от старта api; проба в контейнере — код 0; из сети стека http://api:8107/api/healt…
2026-11-20T10:12:01Z A66 повтор migrate — ок: код 0, ревизия ['0001'], «Running upgrade» нет
2026-11-20T10:12:03Z A69 остановка и старт db — ок: unhealthy через 25.7 с после остановки db, healthy через 4.6 с после возврата db; перезапусков 0
2026-11-20T10:12:39Z A67 неверный пароль у migrate — ок: up --wait — код 1; migrate — код выхода 1; api — created
2026-11-20T10:12:47Z уборка: своего не осталось
2026-11-20T10:12:47Z итог: шагов 13, красных 0
exit=0
```

- Счёт — 2313 строк и 16 файлов при пределах 2500 и 50; порог 2300 перейдён с разрешения
  проверяющего (находки приёмки), потолок 2450.
- WARNING «A64, A66 без ссылки» — диапазон «A60–A67» в строке живого прогона гейт не читает;
  строка поправлена перечнем коммитом этого отчёта. «Реляционный оракул» — долг В1;
  «ci-oracles: weak» — CI нет до В1, вместо ссылки — этот прогон.
- После прогона своего стека нет: `docker ps -a`, `docker volume ls`, `docker network ls` и
  образы с именем стека клона — по 0. Тестовая база клона снята, клон удалён.

## Пробный прогон гейтов В1а

Временный клон вершины, `git merge --no-commit` вершины `contour-b1a` (`1ac5a09`), конфликты —
только в `delivery/active/` (взяты наши), `cd backend && uv sync --frozen`, затем
`pre-commit run --all-files` окружением копии; без `--no-verify`. Первый прогон (на `980f679`)
нашёл: ruff S105, N818, SIM105, S603; mypy strict — `probe.py`, `stack_rules.py`, оракул;
file-length — `scripts/stack_oracle.py` 722 строки при пределе 500; detect-secrets — две
выдуманные строки тестов и `MASTER_KEY_SECRET`. Правки — `eeda8b1`, `3cf0233` (разрез оракула:
`scripts/stack_oracle_compose.py`), `7d599b3`. Итог на `7d599b3` + `1ac5a09`:

```
check yaml … Passed
check json … Passed
check toml … Passed
check for merge conflicts … Passed
check for added large files … Passed
тексты контура не коммитятся (вариант D) … (no files to check)Skipped
ruff check (версия из lock проекта) … Passed
ruff format (версия из lock проекта) … Passed
mypy strict (весь пакет, один вызов) … Passed
ruff check — deploy/ и свои скрипты (конфиг backend) … Passed
ruff format — deploy/ и свои скрипты (конфиг backend) … Passed
mypy strict — deploy/ и свои скрипты (конфиг backend) … Passed
Файлы кода <= 500/1000 строк (baseline-ratchet) … Passed
config-access — типизированный config.X, не getattr/os.getenv … Passed
di-indirection — без pkg/session/ctx Any и importlib reach-back … Passed
service-no-web — сервис-слой не импортирует web-фреймворк … Passed
no-grab-bag — utils/misc/common/helpers.py без темы запрещены … Passed
silent-except — broad-except без raise/лога (AST) … Passed
inline-prompt — LLM-промпт инлайном в .py (AST) … Passed
cpu-in-async — разбор/регулярка в цикле внутри async def (AST) … Passed
unbounded-list — эндпоинт отдаёт список без границы (AST) … Passed
DRY — рост копипаста (jscpd clone-count ratchet) … Passed
layers — направление зависимостей (import-linter / dependency-cruiser) … Passed
function complexity ratchet (C901/PLR09xx) … Passed
error messages carry context … Passed
logs are structured (searchable by field) … Passed
secrets — ничего секретного в коммит (§2.7) … Failed
every gate script is wired into pre-commit/CI … Passed
```

- Единственное красное — detect-secrets на `delivery/active/spec.md:125`: подписанная строка
  A64 с `ENV JWT_SECRET=$JWT_SECRET`. Подписанный файл не правится; запись в `.secrets.baseline`
  и строка в decisions — коммитом после слияния main с В1а в ветку (решение проверяющего).
- Две пометки `# pragma: allowlist secret` в `backend/tests/test_stack_rules.py` — у каждой
  своя строка в decisions: файл, значение, почему вид нужен.

## Порчи

- Проверяющего, 26 (M1–M16, P1–P3, O1–O7), копией его скрипта после разреза оракула — путь O5
  во втором куске, фрагменты остальных не сдвинулись, «НЕ ПРИМЕНЕНА» — ни одной:

```
контроль до: код 0 — 93 passed in 7.57s
контроль после: код 0 — 93 passed in 7.61s
итог: убито 26, выжило 0
```

- Свои: правила (срез 1) — 29 из 29; проба (срез 2) — 8 из 8; находки 1–5 — 5 из 5 и 1 из 1;
  оракул образа во временном клоне (срез 3) — 8 из 8, чистые части — 7 из 7; стек (срез 4) —
  6 из 6. Списки — `tasks.md`.

## Чего защита не ловит

- Статической проверки адреса прослушивания api до 5б нет: правило `api-listen` придёт с web
  (решение проверяющего). Петлю на настоящем файле ловит шаг A69 оракула на каждом прогоне —
  запрос `http://api:<порт>/api/health` из разового контейнера в сети стека; правку compose в
  обход репозитория — сверка после выката (Ч1.6).
- Форма вывода `config --no-interpolate` у Compose 2.38.2 (раннер) и 5.5.1 (сервер) не
  проверена — открытое в decisions; на раннере её покажет джоба «Оракула стека в CI», на
  сервере — пробный файл проверяющего и `check` 6а.
- Сверка версии uv с настоящим `quality.yml` — после слияния В1а; до него — «файлов CI нет»
  словами в выводе теста и оракула.

## Verdict

За проверяющим: свой прогон оракула, полный набор, повтор порч, скан A23.

**Убрано (CONSTITUTION, п. 10):** временные клоны (чистый, пробный под В1а, клон порч) и тестовая
база чистого клона; пробные образы и стеки оракула — «своего не осталось» после каждого прогона;
скачанные данные реестров; `__pycache__` вне `.venv`. Остаётся до сдачи: тестовая база рабочей
копии — для повторов, снимается после поставки.
