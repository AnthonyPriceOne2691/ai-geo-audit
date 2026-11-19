# Active delivery status

- **slug:** ch1-02d-netguard
- **stack:** delivery@2.21, cqg@absent, okf@absent
- **stack-selftest:** external (корень проекта, вне git: копии из ~/Documents/Prepare @ 5d82ab1)
- **class:** S
- **kind:** feature
- **repro_test:** n/a reason=не bugfix
- **diagnosis:** n/a reason=не bugfix
- **phase:** tasks
  <!-- Срез 5 Ч1.2в (`dd8e15d`) ушёл сюда на контрольной точке T11 Ч1.2в. Код — после коммита
       проверяющего с подписью владельца под примерами A30 и A31 (из Ч1.2в, 08:59Z). -->
- **builder:** agent:hands-session
- **verifier:** agent:lead-session
- **human_ok_spec:** deferred (reason=подпись владельца под примерами A30 и A31 дана в Ч1.2в 2026-11-19T08:59Z; вносит проверяющий своим коммитом: порядок владельца 2026-11-18T13:25:47Z)
- **human_ok_plan:** n/a reason=класс S
- **shape-oracles:** weak
  <!-- CQG не развёрнут: волна В1. Строки до 100 знаков и форма как у ruff format
       соблюдаются руками. -->
- **behavior-oracles:** tests-present — pytest на настоящем Postgres 16: запрет сети — с импорта `conftest.py`, подмены распознавателя считают вызовы; вычистка окружения — подпроцессом pytest с грязным окружением и из каталога с `.env`; предупреждения — ошибки
  <!-- Долг на волну В1: реляционный оракул `@given` вместе с мутационным гейтом CQG. До В1
       его роль — ручные порчи без кэша байткода. -->
- **artifact_oracle:** n/a reason=поставка ничего не собирает; оракул на собранное придёт с образом Docker (Ч1.5)
- **ci-oracles:** weak
  <!-- CI — волна В1. Вместо ссылки на прогон — гейт в чистом клоне в verify-report. -->
- **worktree:** .claude/worktrees/ch1-02d
- **parallel_sessions:** manual-deploy
  <!-- Сессии и субагенты исполнителя; полоса фронта — в своей рабочей копии. Интеграция в main
       до В1 — процессом: только принятое проверяющим, строго по очереди. -->
- **hooks:** not-deployed
- **blockers:** none
- **waivers:** none
- **new_dependency:** none reason=стандартные `socket`, `asyncio` и `ipaddress`
- **runtime_paths:** none reason=поставка трогает только тесты и README: путей, видимых лишь исполнением в бою, в ней нет
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
       строк вместе с тестами». Остальные предохранители — по умолчанию из
       AGENT_DELIVERY_HARNESS.md §3.4. -->
