# Active delivery status
- **slug:** ch1-05c-stop-prod
- **stack:** delivery@2.30, cqg@3.30, okf@absent
- **stack-selftest:** external (тексты канона в корне рабочей копии, вне git; ревизия 212443d)
- **class:** L
  <!-- Предложение исполнителя, решает проверяющий: поставка несёт боевое наложение — файл
       первого выката на общий сервер (лимиты, журнал, потолок Redis), а выкат — в списке
       владельца на полную приёмку (слово 2026-11-19); подписанный план уже есть — срезы 5–6
       плана 5б, слово 2026-11-19T11:32Z, — так что L новой подписи плана не требует.
       Вариант M — если наложение считать подготовкой: живьём его поднимает только Ч1.6. -->
- **kind:** feature
- **repro_test:** n/a reason=не bugfix
- **diagnosis:** n/a reason=не bugfix
- **phase:** tasks
- **builder:** agent:hands-session
- **verifier:** agent:lead-session
- **human_ok_spec:** no
  <!-- Примеры A88 и A89 и строка A87 о счёте 24 — подписанный текст спеки 5б (слово владельца
       2026-11-19T11:32Z); разрез 5б — слова 2026-11-23T14:06:40Z и 14:12:15Z. Подпись или
       ссылку на прежнее слово вносит проверяющий. -->
- **human_ok_plan:** no
- **shape-oracles:** cqg-deployed
- **behavior-oracles:** tests-present — pytest без Docker: правила стека и разбор остановки со свойством; оракул стека на машине и в CI — живые шаги A88 и правила с боевым наложением (A89)
- **artifact_oracle:** scripts/stack_oracle.py — живые шаги остановки и падения api и правила на основном файле с боевым наложением
- **ci-oracles:** deployed
- **worktree:** .claude/worktrees/ch1-05c
- **parallel_sessions:** manual-deploy
- **hooks:** not-deployed
- **blockers:** none
- **waivers:** none
- **runtime_paths:** docker-compose.prod.yml
- **irreversible_surfaces:** публикация репозитория (публичный GitHub — создание и push), выкат на общий сервер, трата на платных сервисах выше $5 за одно действие, выпуск отчёта клиенту
  <!-- Подпись объявления — окном `scripts/sign_irreversible.sh` касанием владельца по этой
       поставке (слово владельца 2026-11-24T08:47:28Z); файлы подписи вносит проверяющий. -->
- **model_surface:** n/a reason=модель продукт не вызывает; поверхность объявляется с первым вызовом модели — волна В3 (§14)
- **rule_enforcers:** n/a reason=model_surface не объявлена
- **canon_drift_waiver:** no
- **baseline_growth_waiver:** no
- **observability:** 1
- **observe_signal:** <заполняется на handoff>
- **observe_until:** <заполняется на handoff>
- **circuit_breakers:** max_loc_diff=2500 max_files_touched=50
