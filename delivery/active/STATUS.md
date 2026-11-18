# Active delivery status

- **slug:** ch1-02b-models-migrations
- **stack:** delivery@2.21, cqg@absent, okf@absent
- **stack-selftest:** external (корень проекта, вне git: копии из ~/Documents/Prepare @ 5d82ab1)
- **class:** M
- **kind:** feature
- **repro_test:** n/a reason=не bugfix
- **diagnosis:** n/a reason=не bugfix
- **phase:** tasks
  <!-- Спека, план и задачи готовы. Код — после коммита проверяющего с подписью владельца
       под примерами. -->
- **builder:** agent:hands-session
- **verifier:** agent:lead-session
- **human_ok_spec:** deferred (reason=подпись владельца под примерами вносит проверяющий своим коммитом: порядок владельца 2026-11-18T13:25:47Z — на подпись даёт и подпись записывает ведущая сессия после своей проверки, at=2026-11-18T14:20Z)
- **human_ok_plan:** n/a reason=класс M
- **shape-oracles:** weak
  <!-- CQG не развёрнут: волна В1 сразу после Ч1.4. Строки до 100 знаков и форма как у
       ruff format соблюдаются руками. -->
- **behavior-oracles:** tests-present — pytest на настоящем Postgres 16: тестовая база строится `alembic upgrade head` подпроцессом той же командой, что в бою; у каждой проверки — проба, на которой она краснеет; предупреждения — ошибки
  <!-- Долг на волну В1, как у Ч1.1 и Ч1.2а: реляционный оракул `@given` вместе с
       мутационным гейтом CQG. До В1 его роль — ручные мутанты без кэша байткода. -->
- **artifact_oracle:** n/a reason=поставка ничего не собирает; оракул на собранное придёт с образом Docker (Ч1.5)
- **ci-oracles:** weak
  <!-- CI — волна В1. Вместо ссылки на прогон — гейт в чистом клоне в verify-report. -->
- **worktree:** .claude/worktrees/ch1-02b
- **parallel_sessions:** manual-deploy
  <!-- Две сессии и субагенты исполнителя. Выкат — отдельным словом владельца. Интеграция
       в main до В1 — процессом: только принятое проверяющим; merge-guard придёт в В1. -->
- **hooks:** not-deployed
- **blockers:** none
- **waivers:** none
- **new_dependency:** alembic reason=миграции схемы: цепочка ревизий, откат, сверка моделей с базой; версия 1.20.1, граница `>=1.20,<1.21` (согласована проверяющим), Mako 1.4.3 приходит с ним by=human:owner (слово «Да, ставь» 2026-11-18T12:16:25Z на вопрос о пакетах Ч1.2)
- **runtime_paths:** backend/migrations/
  <!-- Стартовый набор канона для проекта с базой (§12.6): цепочка миграций с нуля и
       модели ⇄ миграции. Исполняются тестами A17, A18, A22 — тестовая база строится
       `alembic upgrade head` на пустой схеме, сверка `compare_metadata` идёт против
       базы, поднятой цепочкой. Блок «Исполнение рисковых путей» — в verify-report. -->
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
       строк вместе с тестами». Гейт считает и тесты, а они у нас — около 70% правки.
       Остальные предохранители — по умолчанию из AGENT_DELIVERY_HARNESS.md §3.4. -->
