# Active delivery status

- **slug:** ch1-12a-settings
- **stack:** delivery@2.30, cqg@3.30, okf@absent
  <!-- Ветка — от main `aefde68` (smoke). -->
- **stack-selftest:** external (корень проекта, вне git: копии из ~/Documents/Prepare @ 212443d)
- **class:** M
  <!-- Класс — решение проверяющего 2026-11-20T09:42Z: M, полная приёмка по признаку «секреты»;
       выката, платного вызова и сети наружу нет. -->
- **kind:** feature
- **repro_test:** n/a reason=не bugfix
- **diagnosis:** n/a reason=не bugfix
- **phase:** specify
  <!-- Следующая поставка третьей после smoke — решение ведущей 2026-11-24. Черновик «рук» от
       2026-11-20 (локальные `docs/deliveries/ch1-12a-spec.md` и `ch1-12a-plan.md`) освежён
       исполнителем на main `aefde68` 2026-11-24T12:48Z: ревизия, `schema.py` и её потребители,
       оснастка тестов, слои, оракул, `env-init`, зависимость, уроки L54–L121. Черновики — ведущей
       на чтение; код — после подписи владельца; приёмка — вторая проверяющая. -->
- **builder:** agent:front-session
- **verifier:** agent:reviewer2-session
- **human_ok_spec:** pending
- **human_ok_plan:** n/a reason=класс M
- **shape-oracles:** cqg-deployed
- **behavior-oracles:** tests-present — pytest на настоящем Postgres 16; сценарии smoke — `delivery/evals/smoke/run.sh` (S3, S1, S2)
- **artifact_oracle:** `scripts/stack_oracle.py` — образ с новым пакетом, api в бою с мастер-ключом нового формата (A60, A65 Ч1.5а)
- **ci-oracles:** deployed
- **worktree:** .claude/worktrees/smoke-run
  <!-- Копия smoke переведена на ветку `ch1-12a-settings` от `aefde68`: новую копию в общем
       `.claude/worktrees/` изолированная сессия исполнителя не заводит. Снимается на сдаче. -->
- **parallel_sessions:** manual-deploy
- **hooks:** not-deployed
- **blockers:** none
- **waivers:** none
- **runtime_paths:** backend/src/ai_geo_audit/settings/
- **irreversible_surfaces:** публикация репозитория (публичный GitHub — создание и push), выкат на общий сервер, трата на платных сервисах выше $5 за одно действие, выпуск отчёта клиенту
  <!-- Строка — прежняя, до новой строки о тратах с Ч1.5в (решение ведущей 2026-11-24). Подпись
       объявления — окном `scripts/sign_irreversible.sh` касанием владельца, по поставке. -->
- **model_surface:** n/a reason=модель продукт не вызывает; поверхность объявляется с первым вызовом модели — волна В3 (§14)
- **rule_enforcers:** n/a reason=model_surface не объявлена
- **canon_drift_waiver:** no
- **baseline_growth_waiver:** no
- **observability:** 1
- **observe_signal:** на первых запросах после слияния: `tests` с тестами A220–A238, `stack` — api в бою стартует с мастер-ключом нового формата (A65 Ч1.5а), S3 smoke судит таблицы `service_keys` и `setting_versions`
- **observe_until:** pending — назначается на сдаче
- **circuit_breakers:** max_loc_diff=2500 max_files_touched=50
  <!-- Предел 2500 строк вместе с тестами и 50 файлов — слова владельца 2026-11-19T14:26Z и
       14:36Z; порог «сказать до сдачи» — 2300. -->
