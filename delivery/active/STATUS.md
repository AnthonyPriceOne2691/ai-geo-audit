# Active delivery status

- **slug:** ch2-02-check-sets
- **stack:** delivery@2.30, cqg@3.30, okf@absent
- **stack-selftest:** external (тексты канона в корне рабочей копии, вне git; ревизия 212443d)
- **class:** M
  <!-- Класс — ответ проверяющего 2026-11-20T09:42Z (L35): три таблицы с триггером, ревизия,
       каталоги и оснастка тестов в нескольких файлах — по работе, а не по объёму; приёмка
       полная — база. Принимает вторая проверяющая — полоса «Части 2–3 без HTTP», слово
       владельца 2026-11-26T08:53:28Z, пункт 1. -->
- **kind:** feature
- **repro_test:** n/a reason=не bugfix
- **diagnosis:** n/a reason=не bugfix
- **phase:** plan
  <!-- Полоса — слово владельца 2026-11-26T08:53:28Z «да на всё» на список ведущей, пункт 1:
       Ч2.1 → Ч2.1б → Ч2.2 → Ч3.1 → Ч3.6 → Ч3.7. Черновики «рук» 2026-11-20 (локальные
       `docs/deliveries/ch2-02-spec.md` и `ch2-02-plan.md`) освежены исполнителем полосы на main
       `fac9427` 2026-11-27 по заданию ведущей; разрез ведущей 2026-11-27: здесь A320–A322, A334,
       A337, A338, жизнь набора — Ч2.2б; очередь — Ч2.2 → Ч2.2б → Ч3.1. Прочитаны ведущей
       2026-11-27 — приняты с двумя правками, внесены; заметки второй проверяющей к срезу 0
       (12:52Z) — все семь по решению ведущей, внесены. Ветка — от main `fac9427`. Код — после
       подписи владельца. -->
- **builder:** agent:parts-2-3-session
- **verifier:** agent:reviewer2-session
- **human_ok_spec:** pending
  <!-- Подпись владельца под примерами A320–A322, A334, A337, A338 и A23 вносит ведущая своим
       коммитом. Строки `new_dependency` нет: новых пакетов нет, правило A104 без неё выполнено
       (L190). -->
- **human_ok_plan:** n/a reason=класс M
- **shape-oracles:** cqg-deployed
- **behavior-oracles:** tests-present — pytest на настоящем Postgres 16: таблицы, ограничения и триггер неизменности, каталоги, встроенный набор и оснастка, коды версии и блок AI-видимости, гонка двух `ensure_all_checks` на двух соединениях; живой шаг — `eval-smoke.md`
- **artifact_oracle:** `scripts/stack_oracle.py` — стек поднимается, migrate кладёт `0004` под ролью миграций, api healthy; живой шаг под ролью сервиса — `eval-smoke.md`: встроенный набор виден, сырая правка состава активной версии — отказ `check_set_frozen`
- **ci-oracles:** deployed
- **worktree:** .claude/worktrees/parts-2-3
  <!-- Копия полосы «Части 2–3 без HTTP» (`EnterWorktree` `parts-2-3`, 2026-11-26); ветка
       `ch2-02-check-sets` — от main `fac9427` 2026-11-27 (Ч2.1б, №40). -->
- **parallel_sessions:** manual-deploy
- **hooks:** not-deployed
- **blockers:** none
- **waivers:** none
- **runtime_paths:** backend/migrations/versions/
  <!-- Путь исполнения один (L177): ревизию `0004` под настоящими ролями видит только стек —
       migrate под ролью миграций (шаг `stack` CI) и живой шаг под ролью сервиса. У `sets/` входа
       нет: его зовут тесты, потом Ч2.2б и Ч2.3. Ключ `CHECK_SETS` в `storage/locks.py` —
       константа реестра, различие ключей держит тест A19. -->
- **irreversible_surfaces:** публикация репозитория (публичный GitHub — создание и push), выкат на общий сервер, любая трата на платных сервисах, выпуск отчёта клиенту
  <!-- Строка — как в `delivery/CONSTITUTION.md` и STATUS Ч2.1б. Подпись объявления — окном
       `scripts/sign_irreversible.sh` касанием владельца, по поставке. -->
- **model_surface:** n/a reason=модель продукт не вызывает; поверхность объявляется с первым вызовом модели — волна В3 (§14)
- **rule_enforcers:** n/a reason=model_surface не объявлена
- **canon_drift_waiver:** no
- **baseline_growth_waiver:** no
- **observability:** 1
- **observe_signal:** первый прогон CI на main после слияния Ч2.2: `tests` — A320–A322, A334, A337, A338 зелёные; доли порч — пакет `sets` одной долей в бюджете 1200 с; `stack` — migrate кладёт `0004` под ролью миграций, api healthy
- **observe_until:** pending — назначается на сдаче
- **circuit_breakers:** max_loc_diff=2500 max_files_touched=50
  <!-- Предел 2500 строк вместе с тестами и 50 файлов — слова владельца 2026-11-19T14:26Z и
       14:36Z; порог «сказать до сдачи» — 2300. -->
