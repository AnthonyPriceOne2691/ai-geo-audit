# Active delivery status

- **slug:** smoke-run
- **stack:** delivery@2.30, cqg@3.30, okf@absent
  <!-- Ветка — от main `fe0f367` («Скорость CI»). -->
- **stack-selftest:** external (корень проекта, вне git: копии из ~/Documents/Prepare @ 212443d)
- **class:** M
  <!-- Класс — ответ ведущей 2026-11-23T13:54Z: правка CI и адаптация шаблона канона, примеры к
       подписи владельца; код сервиса (`backend/src`) не меняется. -->
- **kind:** feature
- **repro_test:** n/a reason=не bugfix
- **diagnosis:** n/a reason=не bugfix
- **phase:** plan
  <!-- Долг В1а (L73), порядок полосы — слово владельца 2026-11-23T09:48Z. Черновик спеки и плана —
       `1c2b228`; правки по ответам ведущей — `df18aac`; по чтению второй проверяющей — `9c352cc`;
       по решениям ведущей 14:08Z — `cee7718`; по второму чтению и решениям ведущей 14:19Z —
       `863e9fb`. Обе проверяющие дочитали `863e9fb`, замечаний нет. -->
- **builder:** agent:front-session
- **verifier:** agent:reviewer2-session
- **human_ok_spec:** no
  <!-- Подпись вносит ведущая: подписи и вопросы владельцу по полосе третьей сессии — у ведущей
       (слово владельца 2026-11-20T09:49Z). -->
- **human_ok_plan:** n/a reason=класс M
- **shape-oracles:** cqg-deployed
- **behavior-oracles:** tests-present — pytest на настоящем Postgres 16; сценарии smoke — `delivery/evals/smoke/run.sh` (S3, S1, S2); шаг smoke на GitHub — ветка-проба
- **artifact_oracle:** n/a reason=поставка ничего не собирает
- **ci-oracles:** deployed
  <!-- CI проекта — в main с В1а (`9ba7d93`); защита main — шесть обязательных проверок с
       2026-11-23 (A116): `gates`, `delivery`, `tests`, `mutation`, `stack`, `image-scan`. Запрос
       поставки судит настоящий прогон. -->
- **worktree:** .claude/worktrees/smoke-run
- **parallel_sessions:** manual-deploy
- **hooks:** not-deployed
- **blockers:** none
- **waivers:** none
- **runtime_paths:** .github/workflows/
  <!-- Шаг smoke в джобе `tests` и адаптированный шаг «Smoke evals» джобы `delivery` исполняются
       только на раннере GitHub: доказательство — прогон ветки-пробы, строка eval-smoke со ссылкой
       на прогон. -->
- **irreversible_surfaces:** публикация репозитория (публичный GitHub — создание и push), выкат на общий сервер, трата на платных сервисах выше $5 за одно действие, выпуск отчёта клиенту
- **model_surface:** n/a reason=модель продукт не вызывает; поверхность объявляется с первым вызовом модели — волна В3 (§14)
- **rule_enforcers:** n/a reason=model_surface не объявлена
- **canon_drift_waiver:** no
- **baseline_growth_waiver:** no
- **observability:** 1
- **observe_signal:** на первых запросах после слияния: шаг smoke в джобе `tests` — S3 и S1 зелёные, итог «сценариев 2 из 3, пропущен S2» с причиной; `delivery_check` без предупреждения §6.1; шаг «Smoke evals» джобы `delivery` печатает, где идёт smoke; `tests` укладывается в 15 минут; на сдаче следующей поставки `merge_guard` гоняет smoke в слитом дереве — путь пакета в первой строке вывода из этого дерева, после прогона нет базы smoke и базы набора того дерева
- **observe_until:** 2026-11-28 — первые запросы и первая сдача после слияния
- **circuit_breakers:** max_loc_diff=2500 max_files_touched=50
  <!-- Предел 2500 строк вместе с тестами и 50 файлов — слова владельца 2026-11-19T14:26Z и
       14:36Z; порог «сказать до сдачи» — 2300. -->
