# Active delivery status

- **slug:** ch1-04ce-client-link
- **stack:** delivery@2.30, cqg@3.30, okf@absent
- **stack-selftest:** external (корень проекта, вне git: копии из ~/Documents/Prepare @ 212443d)
- **class:** L
  <!-- Класс — новый пакет в сборке (`@tanstack/react-query`), новый пакет тестов, сервер
       разработки и первый сетевой код фронта. Приёмка — вторая проверяющая (полоса третьей). -->
- **kind:** feature
- **repro_test:** n/a reason=не bugfix
- **diagnosis:** n/a reason=не bugfix
- **phase:** plan
  <!-- Полоса фронта — слово владельца 2026-11-26T08:53:28Z «да на всё» на список ведущей:
       «Схема» → В1б → «Клиент API и связь» → 4г. Черновики 19.11 4в и 4д
       (`docs/deliveries/ch1-04c-*`, `ch1-04e-*`) сведены и освежены исполнителем на main
       `3500a65` 2026-11-26 по заданию ведущей; освежены снова 2026-11-27 на main `e2707ae` и
       ветке В1б (`871afe3`). Черновики — ведущей на чтение; код — после подписи владельца: спека,
       план, два пакета, решение по цепочке Stryker. -->
- **builder:** agent:front-session
- **verifier:** agent:reviewer2-session
- **human_ok_spec:** pending
- **human_ok_plan:** pending
  <!-- Строки `new_dependency` двух пакетов — после слова владельца, с `by=human:owner`. -->
- **shape-oracles:** cqg-deployed
  <!-- TS-половины гейтов — с В1б: она в main раньше этой поставки. -->
- **behavior-oracles:** tests-present — Vitest и jsdom: клиент, договор, связь на заглушке; среда Node: перенаправление на сокетах, сервер разработки; живой прогон с API; снимки в обеих схемах; шаг через web — `eval-smoke.md`
- **artifact_oracle:** frontend/scripts/check-dist.mjs
- **ci-oracles:** deployed
  <!-- Джоба `frontend` В1б на каждом запросе. -->
- **worktree:** .claude/worktrees/smoke-run
  <!-- Копия — решение ведущей; новую копию в общем `.claude/worktrees/` изолированная сессия
       исполнителя не заводит. -->
- **parallel_sessions:** manual-deploy
- **hooks:** not-deployed
- **blockers:** none
- **waivers:** none
- **runtime_paths:** frontend/
  <!-- Связь видит только исполнение — живой прогон и проба в браузере; сервер разработки —
       `frontend/vite.config.ts`. -->
- **irreversible_surfaces:** публикация репозитория (публичный GitHub — создание и push), выкат на общий сервер, любая трата на платных сервисах, выпуск отчёта клиенту
  <!-- Строка — как в `delivery/CONSTITUTION.md`. Подпись объявления — окном
       `scripts/sign_irreversible.sh` касанием владельца, после слова на спеку. -->
- **model_surface:** n/a reason=модель продукт не вызывает; поверхность объявляется с первым вызовом модели — волна В3 (§14)
- **rule_enforcers:** n/a reason=model_surface не объявлена
- **canon_drift_waiver:** no
- **baseline_growth_waiver:** no
- **observability:** 1
- **observe_signal:** первый прогон CI после слияния — джоба `frontend` зелёная, `npm audit` — 0, снимок JS-аудита — 0; на первом выкате — заглушка через web говорит «Сервер отвечает», в панели сети запрос `/api/health` без `Authorization` от клиента и с `no-store`; api остановлен — текст образца `proxy-bad-gateway`
- **observe_until:** pending — назначается на сдаче
- **circuit_breakers:** max_loc_diff=2500 max_files_touched=50
  <!-- Предел 2500 строк вместе с тестами и 50 файлов — слова владельца 2026-11-19T14:26Z и
       14:36Z; порог «сказать до сдачи» — 2300; точка на шве — решение ведущей 2026-11-26. -->
