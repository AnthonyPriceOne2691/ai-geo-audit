# Active delivery status

- **slug:** ch1-05-stack-ci
- **stack:** delivery@2.21, cqg@absent, okf@absent
  <!-- Ветка — от вершины сборки стека Ч1.5а `76abe44` (решение проверяющего 2026-11-20T09:41Z):
       В1а с осью ② ещё не в main; cqg придёт слиянием main после публикации В1а. -->
- **stack-selftest:** external (корень проекта, вне git: копии из ~/Documents/Prepare @ 5d82ab1)
- **class:** L
  <!-- L, а не M: в CI попадает сторонний бинарник и сетевая база уязвимостей — работа рискованная
       (Delivery §2.2, «risky/L»); план владелец подписал вместе со строками 2026-11-20T09:38Z. -->
- **kind:** feature
- **repro_test:** n/a reason=не bugfix
- **diagnosis:** n/a reason=не bugfix
- **phase:** plan
- **builder:** agent:front-session
- **verifier:** agent:lead-session
- **human_ok_spec:** deferred (reason=подпись вносит проверяющий своим коммитом со словом владельца 2026-11-20T09:38Z)
- **human_ok_plan:** deferred (reason=подпись вносит проверяющий своим коммитом со словом владельца 2026-11-20T09:38Z)
- **shape-oracles:** weak
- **behavior-oracles:** tests-present — тесты без Docker: таблица шагов оракула, скрипт сканера на подставных архивах, файлы джоб; оракул и скан образа — живыми прогонами
- **artifact_oracle:** scripts/stack_oracle.py и scripts/image_scan.py — образ из чистой копии, скан файлом `docker save`
- **ci-oracles:** weak
- **worktree:** .claude/worktrees/ch1-05-stack-ci
- **parallel_sessions:** manual-deploy
- **hooks:** not-deployed
- **blockers:** none
  <!-- Ч1.5а ещё не в main: гейт против origin/main считает и её дифф — до её публикации свои
       числа поставки — против вершины Ч1.5а. -->
- **waivers:** none
  <!-- Класс L при `ci-oracles: weak` требует `waivers: ci` словом владельца до первого зелёного
       прогона CI на коммите поставки (как у В1а): CI придёт слиянием main с В1а. Просит и вносит
       проверяющий вместе с подписью. -->
- **new_dependency:** none reason=пакетов нет; Trivy 0.76.2 — инструмент CI файлом выпуска по sha256, слово владельца 2026-11-20T09:38Z
- **runtime_paths:** .github/workflows/
- **irreversible_surfaces:** публикация репозитория (публичный GitHub — создание и push), выкат на общий сервер, трата на платных сервисах выше $5 за одно действие, выпуск отчёта клиенту
- **model_surface:** n/a reason=модель продукт не вызывает; поверхность объявляется с первым вызовом модели — волна В3 (§14)
- **rule_enforcers:** n/a reason=model_surface не объявлена
- **canon_drift_waiver:** no
- **baseline_growth_waiver:** no
- **observability:** 1
- **observe_signal:** <заполняется на handoff>
- **observe_until:** <заполняется на handoff>
- **circuit_breakers:** max_loc_diff=2500 max_files_touched=50
