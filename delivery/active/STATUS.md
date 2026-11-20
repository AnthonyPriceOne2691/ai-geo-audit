# Active delivery status

- **slug:** ch1-05a-image-compose
- **stack:** delivery@2.21, cqg@absent, okf@absent
- **stack-selftest:** external (корень проекта, вне git: копии из ~/Documents/Prepare @ 5d82ab1)
- **class:** M
  <!-- Класс по работе (§2.2, §2.2b): образ сервиса, migrate и compose с оракулом стека —
       правила compose, проба здоровья, каркас оракула, `Dockerfile`, compose. Объём по мере ×2 —
       около 1456 строк; одной поставкой — слово владельца 2026-11-19T14:26Z (поставка — тема
       целиком); внутренняя контрольная точка — после коммита кода среза 4, порог 2300
       (plan.md). -->
- **kind:** feature
- **repro_test:** n/a reason=не bugfix
- **diagnosis:** n/a reason=не bugfix
- **phase:** tasks
  <!-- Ветка поверх вершины Ч1.3а по слову проверяющего 2026-11-19T13:27Z. Спека и план —
       подписанные черновики с правками проверяющего 2026-11-19T14:28Z и числами новой меры по
       его слову 2026-11-19T14:48Z: спека `bb2c7a796a1f`, план `fc3dd100bfb6`, таблица
       примеров `4b4378dca18d`. Код — после подписи под документами и среза 0. -->
- **builder:** agent:hands-session
- **verifier:** agent:lead-session
- **human_ok_spec:** deferred (reason=примеры подписаны владельцем; вносит проверяющий своим коммитом при открытии ветки вместе со строкой плана об `API_REDIS_URL` и правилом «правила называть кодом»: порядок владельца 2026-11-18T13:25:47Z)
- **human_ok_plan:** n/a reason=класс M
- **shape-oracles:** weak
  <!-- CQG не развёрнут: волна В1. Строки до 100 знаков и форма как у ruff format
       соблюдаются руками. -->
- **behavior-oracles:** tests-present — pytest без Docker: правила compose, проба здоровья, разбор `Dockerfile`, чистые части каркаса оракула; оракул стека `scripts/stack_oracle.py` на машине с Docker
  <!-- Долг на волну В1: реляционный оракул `@given`. До В1 — ручные порчи без кэша байткода. -->
- **artifact_oracle:** <scripts/stack_oracle.py — скрипт появится в срезе 3 плана>
- **ci-oracles:** weak
  <!-- CI — волна В1; джоба `stack` — «Оракул стека в CI» третьей сессии. Вместо ссылки на
       прогон — гейт и оракул в чистом клоне в verify-report. -->
- **worktree:** .claude/worktrees/ch1-05a
- **parallel_sessions:** manual-deploy
- **hooks:** not-deployed
- **blockers:** none
- **waivers:** none
- **new_dependency:** none reason=пакетов Python нет; базовые образы python 3.12-slim, postgres 16, redis 7.4 и uv 0.11.26 — слово владельца 2026-11-19T10:40Z, тег и дайджест — в decisions (срез 0)
- **runtime_paths:** deploy/backend/
  <!-- Образ и стек видны только исполнением: оракул собирает образ и поднимает стек; вывод —
       в verify-report, блок «Исполнение рисковых путей». -->
- **irreversible_surfaces:** публикация репозитория (публичный GitHub — создание и push), выкат на общий сервер, трата на платных сервисах выше $5 за одно действие, выпуск отчёта клиенту
- **model_surface:** n/a reason=модель продукт не вызывает; поверхность объявляется с первым вызовом модели — волна В3 (§14)
- **rule_enforcers:** n/a reason=model_surface не объявлена
- **canon_drift_waiver:** no
- **baseline_growth_waiver:** no
- **observability:** 1
- **observe_signal:** <заполняется на handoff>
- **observe_until:** <заполняется на handoff>
- **circuit_breakers:** max_loc_diff=2500 max_files_touched=50
  <!-- Слова владельца ведущей сессии: 2026-11-19T14:26Z «Да, согласен. Давай ускоримся» —
       предел 2500 строк вместе с тестами, поставка — тема целиком (список из шести пунктов);
       2026-11-19T14:36Z «да» — предел 50 файлов (список из одного пункта). Порог «сказать до
       сдачи» — 2300. Остальные предохранители — по умолчанию из AGENT_DELIVERY_HARNESS.md §3.4. -->
