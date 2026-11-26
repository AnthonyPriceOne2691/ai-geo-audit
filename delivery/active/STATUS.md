# Active delivery status

- **slug:** ch2-01-thresholds
- **stack:** delivery@2.30, cqg@3.30, okf@absent
- **stack-selftest:** external (корень проекта, вне git: копии из ~/Documents/Prepare @ 212443d)
- **class:** M
  <!-- Класс — черновик «рук» 2026-11-20 и решение проверяющего 2026-11-20T09:42Z (L35): модели,
       ревизия, файл и команда в нескольких файлах — по работе, а не по объёму; приёмка полная —
       база. Принимает вторая проверяющая — задание ведущей по слову владельца
       2026-11-26T08:53:28Z, пункт 1. -->
- **kind:** feature
- **repro_test:** n/a reason=не bugfix
- **diagnosis:** n/a reason=не bugfix
- **phase:** plan
  <!-- Полоса «Части 2–3 без HTTP» — слово владельца 2026-11-26T08:53:28Z «да на всё» на список
       ведущей, пункт 1: Ч2.1 → Ч2.1б → Ч2.2 → Ч3.1 → Ч3.6 → Ч3.7. Черновик «рук» 2026-11-20
       (локальные `docs/deliveries/ch2-01-spec.md` и `-plan.md`) освежён исполнителем полосы на
       main `b4fd595` 2026-11-26: уроки L1–L157, `schema.py` и ревизия `0002` Ч1.12а, роли
       «Учёток-1», вход по образцу `ops.alerts`, «Сверка с ТЗ» с частью «сверх ТЗ», мера по
       L125; граница с Ч2.1б — запись ушла туда, решено ведущей 2026-11-26 (decisions). Черновики
       прочла ведущая (`9624f6d`); код — после подписи владельца. -->
- **builder:** agent:parts-2-3-session
- **verifier:** agent:reviewer2-session
- **human_ok_spec:** pending
  <!-- Подпись владельца под примерами A300–A308, A310, A312 и A23 вносит ведущая своим
       коммитом. Строки `new_dependency` нет: новых пакетов нет, правило A104 без неё выполнено
       (решение ведущей 2026-11-26). -->
- **human_ok_plan:** n/a reason=класс M
- **shape-oracles:** cqg-deployed
- **behavior-oracles:** tests-present — pytest на настоящем Postgres 16; сценарии smoke — `delivery/evals/smoke/run.sh` (S3 видит три новые таблицы); живой шаг команды — `eval-smoke.md`
- **artifact_oracle:** `scripts/stack_oracle.py` — migrate под ролью миграций кладёт ревизию `0003`, api healthy (A65 Ч1.5а)
- **ci-oracles:** deployed
- **worktree:** .claude/worktrees/parts-2-3
  <!-- Копия полосы «Части 2–3 без HTTP» — `EnterWorktree` с именем `parts-2-3` от main
       `b4fd595` по заданию ведущей 2026-11-26; ветка `ch2-01-thresholds`. -->
- **parallel_sessions:** manual-deploy
- **hooks:** not-deployed
- **blockers:** none
- **waivers:** none
- **runtime_paths:** backend/migrations/versions/, backend/src/ai_geo_audit/reference/
  <!-- Ревизию под настоящими ролями видит только стек — migrate под ролью миграций (шаг `stack`
       CI); вход `python -m` в образе api — только исполнение (L123). Каталог `reference/`
       появится срезом 1. -->
- **irreversible_surfaces:** публикация репозитория (публичный GitHub — создание и push), выкат на общий сервер, любая трата на платных сервисах, выпуск отчёта клиенту
  <!-- Строка — как в `delivery/CONSTITUTION.md` и STATUS Ч1.14а1. Подпись объявления — окном
       `scripts/sign_irreversible.sh` касанием владельца, по поставке. -->
- **model_surface:** n/a reason=модель продукт не вызывает; поверхность объявляется с первым вызовом модели — волна В3 (§14)
- **rule_enforcers:** n/a reason=model_surface не объявлена
- **canon_drift_waiver:** no
- **baseline_growth_waiver:** no
- **observability:** 1
- **observe_signal:** первый прогон CI на main после слияния Ч2.1: `tests` — A300–A312 зелёные, ветвь A16 сервиса — сверка с базой; доли порч — пакет `reference` одной долей в бюджете; `stack` — migrate кладёт `0003` под ролью миграций, api healthy
- **observe_until:** pending — назначается на сдаче
- **circuit_breakers:** max_loc_diff=2500 max_files_touched=50
  <!-- Предел 2500 строк вместе с тестами и 50 файлов — слова владельца 2026-11-19T14:26Z и
       14:36Z; порог «сказать до сдачи» — 2300. -->
