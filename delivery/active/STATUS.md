# Active delivery status

- **slug:** ch1-04b-front-theme
- **stack:** delivery@2.21, cqg@absent, okf@absent
- **stack-selftest:** external (корень проекта, вне git: копии из ~/Documents/Prepare @ 5d82ab1)
- **class:** M
- **kind:** feature
- **repro_test:** n/a reason=не bugfix
- **diagnosis:** n/a reason=не bugfix
- **phase:** tasks
  <!-- Спека, план и задачи — в ветке; код — после коммита проверяющего с подписью владельца под
       примерами (T0). Ветка — от принятой вершины Ч1.4а `861384c`; после слияния Ч1.4а —
       перенос на main. -->
- **builder:** agent:front-session
  <!-- Код пишет исполнитель полосы фронта — своя сессия с 2026-11-19 (роль назвал владелец),
       в рабочей копии полосы. -->
- **verifier:** agent:lead-session
- **human_ok_spec:** deferred (reason=подпись владельца под примерами дана 2026-11-19T08:59Z пачкой с Ч1.4а и Ч1.2в; вносит проверяющий своим коммитом после сверки текста с показанным)
- **human_ok_plan:** n/a reason=класс M
- **shape-oracles:** weak
  <!-- CQG не развёрнут: волна В1. Форма — prettier и eslint по шаблонам канона в цепочке
       `npm run check`; строки до 100 знаков. -->
- **behavior-oracles:** tests-present — Vitest и jsdom, не больше двух воркеров, цепочка `npm run check`; контраст — расчётом над токенами; у каждой новой проверки оракула сборки — подложенная порча, на которой она краснеет
  <!-- Вид в браузере jsdom не видит: снимки Chrome без окна в обеих темах — для приёмки
       проверяющего (F20), блок «Исполнение рисковых путей» — в verify-report. -->
- **artifact_oracle:** frontend/scripts/check-dist.mjs
  <!-- Оракул сборки Ч1.4а; эта поставка добавляет в него F6 (наборы знаков шрифта) и F12
       (скрипт схемы блокирующим тегом, фон и цвет текста в CSS сборки). -->
- **ci-oracles:** weak
  <!-- CI — волна В1, проверки фронта — В1б. Вместо ссылки на прогон — гейт и цепочка
       `npm run check` в чистом клоне в verify-report. -->
- **worktree:** .claude/worktrees/ch1-04b
- **parallel_sessions:** manual-deploy
  <!-- Три сессии и их субагенты; полоса фронта — в своей рабочей копии. Интеграция
       в main до В1 — процессом: только принятое проверяющим, строго по очереди. -->
- **hooks:** not-deployed
- **blockers:** none
- **waivers:** none
- **new_dependency:** @fontsource-variable/geologica reason=шрифт Geologica со своего адреса: переменный, наборы латиницы и кириллицы, лицензия OFL-1.1; версия 5.3.0, вышла 2026-07-19 by=human:owner at=2026-11-19T08:28Z (слово владельца через проверяющего)
  <!-- Ставится под `min-release-age=14` из `frontend/.npmrc`; после установки — возраст по всем
       парам lock и число `npm audit` в сдаче. Список одобренных прямых зависимостей —
       `frontend/approved-packages.json`. -->
- **runtime_paths:** frontend/src/
  <!-- Экран: раскладку и вид в обеих темах на 1440 × 900, 390 × 844 и таблицу на 1280 jsdom не
       видит. Исполняется снимками Chrome без окна (F20); блок «Исполнение рисковых путей» — в
       verify-report. -->
- **irreversible_surfaces:** публикация репозитория (публичный GitHub — создание и push), выкат на общий сервер, трата на платных сервисах выше $5 за одно действие, выпуск отчёта клиенту
- **model_surface:** n/a reason=модель продукт не вызывает; поверхность объявляется с первым вызовом модели — волна В3 (§14)
- **rule_enforcers:** n/a reason=model_surface не объявлена
- **canon_drift_waiver:** no
- **baseline_growth_waiver:** no
- **observability:** 1
- **observe_signal:** <заполняется на handoff>
- **observe_until:** <заполняется на handoff>
- **circuit_breakers:** max_loc_diff=1200
  <!-- Настройка проекта по слову владельца ведущей сессии 2026-11-18T13:49Z: «давай 1200
       строк вместе с тестами». Порог «сказать до сдачи» — 1100 (решение проверяющего
       2026-11-18). Остальные предохранители — по умолчанию из AGENT_DELIVERY_HARNESS.md §3.4. -->
