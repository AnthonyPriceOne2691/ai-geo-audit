# Active delivery status

- **slug:** ch2-01b-reference-write
- **stack:** delivery@2.30, cqg@3.30, okf@absent
- **stack-selftest:** external (тексты канона в корне рабочей копии, вне git; ревизия 212443d)
- **class:** M
  <!-- Класс — решение проверяющего 2026-11-20T09:42Z (L35): замок, запись редакции, команда и
       функция правки в нескольких файлах — по работе, а не по объёму; приёмка полная — база.
       Принимает вторая проверяющая — полоса «Части 2–3 без HTTP», слово владельца
       2026-11-26T08:53:28Z, пункт 1. -->
- **kind:** feature
- **repro_test:** n/a reason=не bugfix
- **diagnosis:** n/a reason=не bugfix
- **phase:** plan
  <!-- Полоса — слово владельца 2026-11-26T08:53:28Z «да на всё» на список ведущей, пункт 1:
       Ч2.1 → Ч2.1б → Ч2.2 → Ч3.1 → Ч3.6 → Ч3.7. Черновики «рук» 2026-11-20 (локальные
       `docs/deliveries/ch2-01-spec.md` и `ch2-01b-spec.md`) освежены исполнителем полосы на main
       `1b2587f` 2026-11-26 по заданию ведущей; прочитаны ведущей 2026-11-27 — «по существу —
       да», её правки внесены; ветка — от main `bd59f31`. Код — после подписи владельца. -->
- **builder:** agent:parts-2-3-session
- **verifier:** agent:reviewer2-session
- **human_ok_spec:** pending
  <!-- Подпись владельца под примерами A309, A311, A313–A319 и A23 вносит ведущая своим
       коммитом. Строки `new_dependency` нет: новых пакетов нет, правило A104 без неё выполнено
       (решение ведущей 2026-11-26, L190). -->
- **human_ok_plan:** n/a reason=класс M
- **shape-oracles:** cqg-deployed
- **behavior-oracles:** tests-present — pytest на настоящем Postgres 16: запись файлом, гонки на двух соединениях с ожиданием в `pg_locks`, правка порогов и важности; живой шаг команды — `eval-smoke.md`
- **artifact_oracle:** `scripts/stack_oracle.py` — стек поднимается, api healthy (A65 Ч1.5а); живой шаг в образе api — `eval-smoke.md`: в бою `load` отказывает до базы при пустом манифесте, сверх — `check`, `load` и `status` в dev с манифестом томом (L178)
- **ci-oracles:** deployed
- **worktree:** .claude/worktrees/parts-2-3
  <!-- Копия полосы «Части 2–3 без HTTP» (`EnterWorktree` `parts-2-3`, 2026-11-26); ветка
       `ch2-01b-reference-write` — от main `bd59f31` 2026-11-27 (правка ведущей: основа — main на
       момент открытия). -->
- **parallel_sessions:** manual-deploy
- **hooks:** not-deployed
- **blockers:** none
- **waivers:** none
- **runtime_paths:** backend/src/ai_geo_audit/reference/
  <!-- Путь исполнения один — вход `python -m` в образе api (L123, L177): ревизии базы нет, поля
       заголовка редакции есть с Ч2.1; ключ `REFERENCE` в `storage/locks.py` — константа реестра,
       различие ключей держит тест A19. -->
- **irreversible_surfaces:** публикация репозитория (публичный GitHub — создание и push), выкат на общий сервер, любая трата на платных сервисах, выпуск отчёта клиенту
  <!-- Строка — как в `delivery/CONSTITUTION.md` и STATUS Ч2.1. Подпись объявления — окном
       `scripts/sign_irreversible.sh` касанием владельца, по поставке. -->
- **model_surface:** n/a reason=модель продукт не вызывает; поверхность объявляется с первым вызовом модели — волна В3 (§14)
- **rule_enforcers:** n/a reason=model_surface не объявлена
- **canon_drift_waiver:** no
- **baseline_growth_waiver:** no
- **observability:** 1
- **observe_signal:** первый прогон CI на main после слияния Ч2.1б: `tests` — A309, A311, A313–A319 зелёные; доли порч — пакет `reference` одной долей в бюджете 1200 с; `stack` — оракул без красных, api healthy
- **observe_until:** pending — назначается на сдаче
- **circuit_breakers:** max_loc_diff=2500 max_files_touched=50
  <!-- Предел 2500 строк вместе с тестами и 50 файлов — слова владельца 2026-11-19T14:26Z и
       14:36Z; порог «сказать до сдачи» — 2300. -->
