# Verify report

**Date:** 2026-11-23
**Verifier:** agent:reviewer2-session
**asserts_reviewed_by:** deferred (reason=подпись владельца под утверждениями просит и вносит ведущая после вердикта второй проверяющей; на том же показе — «было — станет» A129)
**Builder evidence:** agent:front-session — прогон в чистом клоне, дайджест утверждений, скан A23,
замер покрытия скриптов, ревью рисковых мест и исполнение рисковых путей ниже; вердикт — за
проверяющей.

**CI run:** запрос №8, журналы прочли ведущая и вторая проверяющая:
- 45142240080 на `0175ff1` (возврат пробы-порчи `migrations/` и тест секрета подписи) — все пять
  джоб success: `stack` — «итог: шагов 13, красных 0», уборка чистая; `image-scan` — 42 с,
  «итог скана: образов 1, красных 0», «итог описи: образов 1, красных 0»;
- 45141680779 на `17f0da0` (вторая порча) — `stack` failure: A65 с причиной от alembic, A62 —
  три файла, «итог: шагов 13, красных 2»; `image-scan` success после zlib1g;
- 45139473562 на `82ed468` (первая порча) — `stack` failure, 42 с, причины от alembic нет —
  находка, закрыта `04be9c8` и `f130a35`; `image-scan` failure по CVE-2026-85091 — закрыта
  `d1180d3`.

Код после них не менялся: main `77b143b` (накат `cqg@3.30` · `delivery@2.30`) влит коммитом
`77baaa2` без конфликтов. Прогон CI на вершине сдачи — после выкладки.
**Commit:** код — `0175ff1`; слияние main — `77baaa2`; STATUS — `91db96c`; прогон в чистом
клоне — на `91db96c`.

## Прогон в чистом клоне

Клон свежий, с ветки `ch1-05-stack-ci` на `91db96c`; база диффа — `origin/main` клона, `77b143b`
(L2). 2026-11-23T09:21:36Z–09:23:02Z, нагрузка 8,77 → 5,74. Вывод дословный, длинные строки
обрезаны знаком «…»:

```
$ git log --format="%h %p %s" -2 && git rev-parse --short origin/main && git merge-base HEAD origin/main
91db96c 77baaa2 docs(delivery): «Оракул» — STATUS: стек delivery@2.30 · cqg@3.30 после слияния main 77b143b
77baaa2 2bb0d38 77b143b Merge commit '77b143bde983…' into ch1-05-stack-ci
77b143b
77b143bde983…
$ python3 scripts/delivery_check.py --require-spec --diff-base origin/main; echo "exit=$?"
§3.1d: номера примеров сверены с архивом — спек 12, новых 9, уже встречались 1: A23 (delivery/a…
breakers: files=11 net_loc=2237 (+2271/-34), excluded=10 (delivery/|knowledge/|scripts/lint/|…
наблюдение §13.1: поставок в архиве 13, окон со сроком 13, истекших без observed.md 0
сквозные проверки §6.1: сценариев в таблице 0 (delivery/evals/smoke/README.md)
WARNING: delivery/evals/smoke/README.md: таблица сценариев пуста, а код уже поставлен — в архи…
delivery_check: 0 error(s), 1 warning(s)
exit=0
$ cd backend && uv sync --frozen -q; echo "uv sync exit=$?"
uv sync exit=0
$ cd backend && uv run pytest -q -p no:cacheprovider -W error | tail -3; echo "pytest exit=${PIPESTATUS[0]}"
⚠ тело 12 запрос(ов) теста шло потоком: свободный текст в нём НЕ судился — чтение съело бы по…
⚠ текст пользователя НЕ судился: ни один тест не отправил свободный текст и не взял canary_te…
собрано 741, прошло 741, пропущено 0
pytest exit=0
$ uv run --project backend pre-commit run --all-files > ../precommit2.log 2>&1; echo "pre-commit exit=$?"; …
pre-commit exit=0
27
$ uv run --project backend pre-commit run --all-files --hook-stage pre-push > ../prepush2.log 2>&1; echo …
pre-push exit=0
5
$ CI=true uv run --project backend python3 scripts/lint/contour_doctor.py | grep -E "contour-doctor:"; e…
contour-doctor: AUTO 40 · WEAK 1 · ABSENT 2 · TOOL 3 · SKIP 17 · DEAD 0
doctor exit=0
$ bash scripts/lint/check_gate_coverage.sh | tail -1; echo "gate-coverage exit=${PIPESTATUS[0]}"
gate-coverage: OK — 16 скрипт(ов), подключено 13, осознанно нет 3; правил сверено 8, n/a 2; прочих…
gate-coverage exit=0
$ git status --short | head -5; echo "status lines: $(git status --short | wc -l | tr -d " ")"
status lines: 0
$ uv run python …/clean-b1a/drop_db.py audit_test_5b70ca160d
audit_test_5b70ca160d: было 1, стало 0
```

- Гейт — 0 ошибок; счёт — 2237 строк и 11 файлов при пределах 2500 и 50, порог 2300 не перейдён.
  Предупреждение одно — новая проверка `delivery@2.30`, §6.1: таблица сквозных сценариев smoke
  пуста. Решение ведущей 2026-11-23T09:20Z: в «Оракул» не вносим, это работа с исполнителем
  `delivery/evals/smoke/run.sh` и примерами под подпись — отдельной поставкой полосы после
  «Скорости CI».
- Обе стадии хуков (L75): pre-commit `--all-files` — код 0, хуков Passed 27; pre-push — код 0,
  Passed 5: крупные файлы, мёртвый код, гейт фазы и счёта, таяние снимков, новая зависимость.
- Доктор без текстов канона, как в CI, — DEAD 0; мета-гейт — OK.
- Тестовая база клона снята, клон удалён.

## Дайджест утверждений

`BASE=origin/main bash scripts/lint/assert_digest.sh` на `91db96c`: примеры спеки — A23, A74–A76,
A124–A129; привязаны к примерам A74, A75, A76, A124–A129; без примера — 24:
- 6 — загрузка скриптов по пути в тестах (`_spec`, `_spec.loader`, `_oracle_spec`,
  `_oracle_spec.loader` не `None`): механика импорта, а не ожидание;
- 12 — перенос вывода оракула и сканера (`guard.WIDTH`, `guard.INDENT`, `guard.wrapped`, метка
  времени UTC в начале строки): строка Non-functional спеки «Время в выводе оракула и сканера —
  UTC с Z. Строки до 100 знаков, кроме строк таблиц» (decisions, 2026-11-20, функция `say`);
- 3 — сканер на подставном `trivy`: `scan.run(…) == 0`, `scan.sbom(…) == 0`, `len(calls) == 4` —
  шаги теста флагов A129, сами флаги привязаны;
- 3 — прочее: зелёная ветка таблицы шагов (`oracle.run_steps(None, steps) == 0`), строка шага
  «A60 Python и инструменты — ок: …», файл джобы — словарь YAML.

asserts_without_example: 24

## A23

Сканер правила LOGIC-10 (рабочий инструмент сессии, вне репозитория) по новым и изменённым
файлам поставки против `77b143b` на `91db96c`: просмотрено 21, совпадение одно — строка A23 спеки
(`delivery/active/spec.md:118`, текст самого правила). Повтор — последним, на коммите сдачи.

## Покрытие скриптов в CI

Покрытие изменённого и мутации CI судит только в `backend/src` (`LINT_PY_SRC`), четыре скрипта
поставки лежат в `scripts/` — CI их не меряет (decisions, 2026-11-20; долг — первой поставке, что
тронет эти скрипты). Мой замер на `91db96c`: тесты оракула (`test_stack_oracle.py`,
`test_oracle_guard.py`, `test_image_scan.py`, `test_oracle_jobs.py`, 118 из 118) под `coverage`,
изменённые операторы против `77b143b`:

| Файл | Изменённых операторов | Покрыто | Замер проверяющей на `9260c17` |
|---|---|---|---|
| `scripts/image_scan.py` | 304 | 287 (94,4 %) | 287 из 304 |
| `scripts/oracle_guard.py` | 72 | 72 (100 %) | 72 из 72 |
| `scripts/stack_oracle.py` | 34 | 32 (94,1 %) | 24 из 34 |
| `scripts/stack_oracle_compose.py` | 8 | 8 (100 %) | 1 из 1 |

- `stack_oracle.py` вырос за ветку красного шага (`c5fa6a7`) и отказ Docker (E1); не покрыты две
  строки живого пути: `run_oracle(name, Docker())` в `main()` и `finish(Run(…))` с настоящим
  Docker — их исполняет джоба `stack` (45142240080: 13 шагов, 0 красных).
- `stack_oracle_compose.py` — восемь операторов `migrate_log` и причины отказа подъёма, все
  покрыты тестом A76 (`MigrateFails`).
- В `image_scan.py` не покрыты 17 строк: настоящий исполнитель `execute()` и `main()` (их
  исполняет джоба `image-scan`), запасная ветка времени базы не в формате RFC 3339, отказ
  `docker` с кодом, вывод не JSON-объектом и разбор окружения Python образа.
- Файлы целиком: `stack_oracle.py` — 58 % из 297, `stack_oracle_compose.py` — 47 % из 159; шаги
  с Docker идут только живым прогоном.

## Чего защита не ловит

Как в спеке («Чего защита не ловит»), с двумя строками этой сверки: глотание итога внутри самой
команды (`set +e`, `bash -c '…; exit 0'`) тест файлов джоб не видит (решение ведущей
2026-11-20T15:05Z); образы db и redis приходят готовыми и не сканируются (решение ведущей
2026-11-23).

## Обязательные проверки и строки к показу владельцу

- `stack` и `image-scan` в обязательные проверки защиты main не входят (`gates`, `delivery`,
  `tests`): красная джоба оракула слияния на стороне GitHub не держит. Включение — настройка по
  слову владельца; ведущая несёт его вместе со списком имён «Скорости CI».
- A129 «было — станет» (`spec.md:117`, колонка «Ожидаемый выход») — у ведущей к показу владельцу
  на сдаче (решение 2026-11-20T15:03Z). До его слова текст спеки не меняется; тест уже судит по
  «станет»: действующие права джобы — свой блок, если он есть, иначе блок сверху `quality.yml`
  (decisions, 2026-11-20; A100 В1а запрещает джобе свой блок прав).
- Время джоб: `stack` — 87 с на `81ec0b9` (прогон 44759153941), `image-scan` — 42 с на `0175ff1`
  (45142240080); не узкое место CI — узкое место порчи в `tests` («Скорость CI»).

## Ревью рисковых мест

- **Безопасность** (`password`, `jwt`, `secret`, `permission`, «парол»). Пароль базы и секрет
  подписи стека (`run.password`, `run.jwt`) — случайные на прогон, в выводе их нет: хвост журнала
  `migrate` в причине отказа подъёма (`migrate_log`) заменяет оба звёздочками, тест A76
  (`MigrateFails`) кладёт в журнал оба значения и ловит каждое. `permissions` — права токена
  джобы `image-scan` только на чтение, тест файлов джоб краснит копию «contents: write»
  (A129). `# pragma: allowlist secret` — у публичного пароля стенда в тестах. Секретов
  репозитория поставка не заводит.
- **Производительность** (`json.loads`, `json.dumps`, `re.compile`, `re.search`). Всё — в скриптах
  CI и тестах: разбор отчётов Trivy, описи и окружения образа (`json.loads`), подставные отчёты
  тестов (`json.dumps`), регулярки разбора `.trivyignore`, имён `Dockerfile` и файлов джоб
  (`re.compile`, `re.search`) — по одному файлу или отчёту на прогон, вне запросов сервиса.
  Цена — время джоб: `stack` 87 с, `image-scan` 42 с.
- **Интеграция** (`urllib`, `httpx`). `urllib.request.urlopen` в `image_scan.py` качает файл
  выпуска Trivy по адресу из скрипта с пределом на чтение (`noqa: S310` с причиной); сумма
  sha256 сверяется до распаковки, отказ убирает недописанный файл (A124). `httpx` — имя пакета
  в подставной описи теста A127 («сканер не видит пакет httpx 0.28.1»), не вызов сети. Сеть у
  оракула — только Docker на раннере и база уязвимостей Trivy.
- **Новые модули** (`image_scan`, `oracle_guard`, `test_image_scan`, `test_oracle_guard`,
  `test_oracle_jobs`). `oracle_guard.py` — общая обвязка оракула и сканера (замок копии, вывод
  `say`, уборка своего имени), 100 % изменённого; `image_scan.py` — установка, скан, опись;
  тесты — без Docker, у каждого номер примера. Вне `backend/src` их судят ruff и mypy strict
  (хуки `ruff-deploy`, `mypy-deploy`), покрытие и мутации — нет (раздел выше).

## Исполнение рисковых путей

`runtime_paths: .github/workflows/` — джобы `stack` и `image-scan` исполняет только раннер
GitHub; доказательство — прогоны запроса №8 (раздел «CI run»): проба-порча `migrations/` красит
`stack` на A65 с причиной от alembic, возврат — зелёный; скан краснел по делу на CVE-2026-85091
(44759153941 на `81ec0b9`) и зелёный после zlib1g (45141680779, 45142240080). Строки eval-smoke
A76 (строки 2–3) и A124–A129 — со ссылками на прогоны.

## Verdict

<вносит проверяющая>
