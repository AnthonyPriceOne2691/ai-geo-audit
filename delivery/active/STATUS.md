# Active delivery status

- **slug:** ch1-14a1-outbound
- **stack:** delivery@2.30, cqg@3.30, okf@absent
- **stack-selftest:** external (корень проекта, вне git: копии из ~/Documents/Prepare @ 212443d)
- **class:** L
  <!-- Класс — черновик «рук» 2026-11-20 и слово ведущей 2026-11-25: L — новая боевая зависимость
       `redis` и пароль Redis в адресе здоровья; приёмка полная — вторая проверяющая. -->
- **kind:** feature
- **repro_test:** n/a reason=не bugfix
- **diagnosis:** n/a reason=не bugfix
- **phase:** plan
  <!-- Следующая поставка третьей после Ч1.12а — решение ведущей 2026-11-25 (очередь к первому
       выкату от 2026-11-24: а1, затем а2). Черновик «рук» от 2026-11-20 (локальные
       `docs/deliveries/ch1-14a1-spec.md` и `ch1-14a1-plan.md`) освежён исполнителем на main
       `69b2a9b` 2026-11-25: уроки L1–L137, долги по путям, номера, оценка (тесты × 3), «Сверка с
       ТЗ», службы CI в двух джобах, «было — станет» П1 (`decisions.md`). Черновики — ведущей на
       чтение; код — после подписи владельца: спека, план, пакет `redis`. -->
- **builder:** agent:front-session
- **verifier:** agent:reviewer2-session
- **human_ok_spec:** yes (by=human:owner at=2026-11-25T12:03Z)
  <!-- Слово владельца 2026-11-25T12:03:52Z — «все оптимально» на список ведущей из восьми
       пунктов (показ — `docs/deliveries/ch1-14a1-owner-show.md`). К этой поставке — пункты 5–8:
       5) подпись спеки и плана, класс L: примеры A240–A243 и A23 на `969c9ed` — файл спеки
       sha256 `0079cd27d8d6`, SHA-1 `50efdff5fcfd`; строк `| A` — 6, sha256 `de61370b2599`, SHA-1
       `a5e94e5e1329`; план — sha256 `b677a7995b40`, SHA-1 `f61bcd552132`; окно подписи
       необратимого — следом, касанием владельца; 6) пакет `redis` 8.1.0 — строка
       `new_dependency` ниже; 7) «было — станет» П1, 15 строк (`decisions.md`): живые спеки
       правит ведущая, архивы в main не правим (В12); 8) чистые клоны T5 и сдачи (T6) — ведущая
       (L137). Внесла ведущая. -->
- **human_ok_plan:** yes (by=human:owner at=2026-11-25T12:03Z)
- **new_dependency:** redis reason=клиент Redis в api — исходящие тревог и Redis в здоровье — 8.1.0, выпуск 2026-07-30 (тег `v8.1.0` и PyPI), последний старше 14 суток, колесо py3-none-any, косвенных для Python 3.12 нет; свой клиент протокола не пишем by=human:owner at=2026-11-25T12:03Z
  <!-- Слово владельца 2026-11-25T12:03:52Z, пункт 6 списка ведущей. Версию на день кода сверить
       ещё раз по первоисточнику (L49, T0): иная версия или лишний пакет в замке — снова словом
       владельца. Внесла ведущая. -->
- **shape-oracles:** cqg-deployed
- **behavior-oracles:** tests-present — pytest на настоящих Postgres 16 и Redis 7.4; сценарии smoke — `delivery/evals/smoke/run.sh` (S3, S1, S2)
- **artifact_oracle:** `scripts/stack_oracle.py` — образ с новым пакетом, api со здоровьем, где есть Redis, — healthy (A65, A69 Ч1.5а)
- **ci-oracles:** deployed
- **worktree:** .claude/worktrees/smoke-run
  <!-- Третья поставка в копии smoke (smoke, Ч1.12а, Ч1.14а1): новую копию в общем
       `.claude/worktrees/` изолированная сессия исполнителя не заводит; копию оставить — решение
       ведущей 2026-11-25. -->
- **parallel_sessions:** manual-deploy
- **hooks:** not-deployed
- **blockers:** none
- **waivers:** none
- **runtime_paths:** backend/src/ai_geo_audit/alerts.py
- **irreversible_surfaces:** публикация репозитория (публичный GitHub — создание и push), выкат на общий сервер, любая трата на платных сервисах, выпуск отчёта клиенту
  <!-- Строка — как в `delivery/CONSTITUTION.md` и STATUS Ч1.12а. Подпись объявления — окном
       `scripts/sign_irreversible.sh` касанием владельца 2026-11-25T12:07Z, по его слову
       12:03:52Z (пункт 5 списка ведущей). Первое окно, 12:07:16Z, — «Отложено»; второе — по его
       слову «давай еще раз» 12:07:33Z. Сверка `check_irreversible_signature.sh` — код 0.
       Запустила ведущая. -->
- **model_surface:** n/a reason=модель продукт не вызывает; поверхность объявляется с первым вызовом модели — волна В3 (§14)
- **rule_enforcers:** n/a reason=model_surface не объявлена
- **canon_drift_waiver:** no
- **baseline_growth_waiver:** no
- **observability:** 1
- **observe_signal:** на первых прогонах после слияния: `tests` и доли порч — со службой Redis, A240–A243 зелёные; `stack` — api healthy со здоровьем, где есть Redis (A65); `check` кладёт запись по форме
- **observe_until:** pending — назначается на сдаче
- **circuit_breakers:** max_loc_diff=2500 max_files_touched=50
  <!-- Предел 2500 строк вместе с тестами и 50 файлов — слова владельца 2026-11-19T14:26Z и
       14:36Z; порог «сказать до сдачи» — 2300. -->
