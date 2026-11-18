# Active delivery status

- **slug:** ch1-02a-db-stand-session
- **stack:** delivery@2.21, cqg@absent, okf@absent
- **stack-selftest:** external (корень проекта, вне git: копии из ~/Documents/Prepare @ 5d82ab1)
- **class:** M
- **kind:** feature
- **repro_test:** n/a reason=не bugfix
- **diagnosis:** n/a reason=не bugfix
- **phase:** specify
- **builder:** agent:hands-session
- **verifier:** agent:lead-session
- **human_ok_spec:** yes (by=human:owner at=2026-11-18T12:51:41Z)
  <!-- Слово владельца в чате исполнителя на одно сообщение: примеры Ч1.2а (A9–A14),
       Ч1.2б (A15–A23) и пункт конституции «Прогон переживает повтор, обрыв и
       параллельный запуск». Ответ кнопкой «Да, подписываю» 12:51:41Z и словами «даю да»
       в ту же минуту. До владельца спеку подтвердил проверяющий. -->
- **human_ok_plan:** n/a reason=класс M
- **shape-oracles:** weak
  <!-- CQG не развёрнут: волна В1 сразу после Ч1.4. Строки до 100 знаков и форма как у
       ruff format соблюдаются руками. -->
- **behavior-oracles:** tests-present — pytest на настоящем Postgres 16: своя тестовая база рабочей копии, два независимых соединения с настоящими фиксациями; предупреждения — ошибки
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
- **new_dependency:** sqlalchemy reason=движок, сессии и модели базы с asyncio, как у соседних сервисов by=human:owner (слово «Да, ставь» 2026-11-18T12:16:25Z)
- **new_dependency:** asyncpg reason=асинхронный драйвер Postgres для SQLAlchemy by=human:owner (слово «Да, ставь» 2026-11-18T12:16:25Z)
- **new_dependency:** pytest-asyncio reason=асинхронные фикстуры движка и сессии, только группа dev by=human:owner (слово «Да, ставь» 2026-11-18T12:27:07Z)
  <!-- alembic — по тому же слову 12:16:25Z, но входит в Ч1.2б, вместе с миграциями. -->
- **runtime_paths:** none reason=миграций в поставке нет: рисковый путь `backend/migrations/` — в Ч1.2б; стенд разработки и движок проверяются тестами на настоящем Postgres
- **irreversible_surfaces:** публикация репозитория (публичный GitHub — создание и push), выкат на общий сервер, трата на платных сервисах выше $5 за одно действие, выпуск отчёта клиенту
- **model_surface:** n/a reason=модель продукт не вызывает; поверхность объявляется с первым вызовом модели — волна В3 (§14)
- **rule_enforcers:** n/a reason=model_surface не объявлена
- **canon_drift_waiver:** no
- **baseline_growth_waiver:** no
- **observability:** 1
- **observe_signal:** <заполняется на handoff>
- **observe_until:** <заполняется на handoff>
- **circuit_breakers:** defaults from AGENT_DELIVERY_HARNESS.md §3.4
