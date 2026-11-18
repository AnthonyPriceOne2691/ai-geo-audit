# Active delivery status

- **slug:** ch1-01-config-logs
- **stack:** delivery@2.21, cqg@absent, okf@absent
- **stack-selftest:** external (корень проекта, вне git: копии из ~/Documents/Prepare @ 5d82ab1)
- **class:** M
- **kind:** feature
- **repro_test:** n/a reason=не bugfix
- **diagnosis:** n/a reason=не bugfix
- **phase:** verify
- **builder:** agent:hands-session
- **verifier:** agent:lead-session
- **human_ok_spec:** yes (by=human:owner at=2026-11-18T14:52+03:00)
  <!-- Слово владельца в чате исполнителя («даю да») на одно сообщение: примеры A1–A8
       простыми словами; четыре ожидания, придуманные исполнителем после кода (A1, A2,
       A5, A6), отмечены прямо; спорное место — значения короче 6 знаков в журнале не
       маскируются — отдельной строкой. До владельца спеку подтвердил проверяющий. -->
- **human_ok_plan:** n/a reason=класс M
- **shape-oracles:** weak
  <!-- CQG не развёрнут: волна В1 сразу после Ч1.4. Строки до 100 знаков и форма как у
       ruff format соблюдены руками, чтобы первый прогон В1 не переписал поставку. -->
- **behavior-oracles:** tests-present — 34 теста pytest по примерам A1–A6, A8; ручные мутанты в verify-report
  <!-- Долг на волну В1, согласован с проверяющим 2026-11-18: реляционный оракул `@given`
       (пакет hypothesis — новое слово владельца) вместе с мутационным гейтом CQG. До В1
       его роль — ручные мутанты. -->
- **artifact_oracle:** n/a reason=поставка ничего не собирает; оракул на собранное придёт с образом Docker (Ч1.5)
- **ci-oracles:** weak
  <!-- CI — волна В1. Вместо ссылки на прогон — гейт в чистом клоне в verify-report. -->
- **worktree:** .claude/worktrees/ch1-01
- **parallel_sessions:** manual-deploy
  <!-- Две сессии и субагенты исполнителя. Выкат — отдельным словом владельца. Интеграция
       в main до В1 — процессом: только принятое проверяющим; merge-guard придёт в В1. -->
- **hooks:** not-deployed
- **blockers:** none
- **waivers:** none
- **new_dependency:** pydantic-settings reason=типизированный конфиг из окружения с проверкой типов и границ, как у соседних сервисов by=human:owner (слово 2026-11-18)
- **new_dependency:** pytest reason=тесты, только группа dev by=human:owner (слово 2026-11-18)
- **runtime_paths:** none reason=конфиг и логи не трогают путей, видимых только исполнением: миграции — Ч1.2, бэкап — Ч1.15, деньги — первый платный клиент
- **irreversible_surfaces:** публикация репозитория (публичный GitHub — создание и push), выкат на общий сервер, трата на платных сервисах выше $5 за одно действие, выпуск отчёта клиенту
- **model_surface:** n/a reason=модель продукт не вызывает; поверхность объявляется с первым вызовом модели — волна В3 (§14)
- **rule_enforcers:** n/a reason=model_surface не объявлена
- **canon_drift_waiver:** no
- **baseline_growth_waiver:** no
- **observability:** 1
- **observe_signal:** <заполняется на handoff>
- **observe_until:** <заполняется на handoff>
- **circuit_breakers:** defaults from AGENT_DELIVERY_HARNESS.md §3.4
