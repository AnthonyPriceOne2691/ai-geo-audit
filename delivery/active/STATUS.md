# Active delivery status

- **slug:** ch1-02-db-migrations
- **stack:** delivery@2.21, cqg@absent, okf@absent
- **stack-selftest:** external (корень проекта, вне git: копии из ~/Documents/Prepare @ 5d82ab1)
- **class:** M
- **kind:** feature
- **repro_test:** n/a reason=не bugfix
- **diagnosis:** n/a reason=не bugfix
- **phase:** specify
- **builder:** agent:hands-session
- **verifier:** agent:lead-session
- **human_ok_spec:** deferred (reason=примеры сначала у проверяющей сессии, затем одним сообщением владельцу вместе с пунктом конституции; at=2026-11-18T12:11Z)
- **human_ok_plan:** n/a reason=класс M
- **shape-oracles:** weak
  <!-- CQG не развёрнут: волна В1 сразу после Ч1.4. Строки до 100 знаков и форма как у
       ruff format соблюдаются руками. -->
- **behavior-oracles:** tests-present — pytest на настоящем Postgres 16: своя тестовая база рабочей копии, два независимых соединения с настоящими фиксациями
  <!-- Долг на волну В1, как у Ч1.1: реляционный оракул `@given` вместе с мутационным
       гейтом CQG. До В1 его роль — ручные мутанты. -->
- **artifact_oracle:** n/a reason=поставка ничего не собирает; оракул на собранное придёт с образом Docker (Ч1.5)
- **ci-oracles:** weak
  <!-- CI — волна В1. Вместо ссылки на прогон — гейт в чистом клоне в verify-report. -->
- **worktree:** .claude/worktrees/ch1-02
- **parallel_sessions:** manual-deploy
  <!-- Две сессии и субагенты исполнителя. Выкат — отдельным словом владельца. Интеграция
       в main до В1 — процессом: только принятое проверяющим; merge-guard придёт в В1. -->
- **hooks:** not-deployed
- **blockers:** none
- **waivers:** none
- **new_dependency:** sqlalchemy reason=движок, сессии и модели базы с asyncio, как у соседних сервисов by=human:owner (слово 2026-11-18T12:16Z)
- **new_dependency:** asyncpg reason=асинхронный драйвер Postgres для SQLAlchemy by=human:owner (слово 2026-11-18T12:16Z)
- **new_dependency:** alembic reason=миграции схемы: с нуля, повторно, откат by=human:owner (слово 2026-11-18T12:16Z)
- **runtime_paths:** backend/migrations/
  <!-- Миграции с нуля — путь, который проверяется только исполнением (§12.6): пустая база,
       `alembic upgrade head`, откат до base и снова вверх, в чистом клоне. -->
- **irreversible_surfaces:** публикация репозитория (публичный GitHub — создание и push), выкат на общий сервер, трата на платных сервисах выше $5 за одно действие, выпуск отчёта клиенту
- **model_surface:** n/a reason=модель продукт не вызывает; поверхность объявляется с первым вызовом модели — волна В3 (§14)
- **rule_enforcers:** n/a reason=model_surface не объявлена
- **canon_drift_waiver:** no
- **baseline_growth_waiver:** no
- **observability:** 1
- **observe_signal:** <заполняется на handoff>
- **observe_until:** <заполняется на handoff>
- **circuit_breakers:** defaults from AGENT_DELIVERY_HARNESS.md §3.4
