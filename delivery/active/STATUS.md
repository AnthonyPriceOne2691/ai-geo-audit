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
- **human_ok_spec:** yes (by=human:owner at=2026-11-27T10:16Z)
- **human_ok_plan:** yes (by=human:owner at=2026-11-27T10:16Z)
  <!-- Слово владельца 2026-11-27T10:16:54Z «Публикуем В1б, подписываю Клиент API и связь —
       вариант (а)» на показ ведущей (`docs/deliveries/ch1-04cd-sign-show.md`), вершина `891ce2b`:
       спека `9454884f7e9d` (302 строки), план `e66c0e11ef34`, строк примеров 25 — `7b5352b898ee`.
       Тем же словом: два пакета (`@tanstack/react-query` в сборку, `@testing-library/user-event`
       в тесты) — строки `new_dependency` с `by=human:owner` и этим временем вносит исполнитель на
       срезе, где пакет ставится, с версией и датой по реестру; цепочка Stryker — вариант (а),
       `overrides` на `qs` 6.16.2 под `typed-rest-client` (decisions). Окно подписи необратимого —
       тем же словом, запускает ведущая. -->
  <!-- Слово владельца 2026-11-27T11:01:24Z «Второй overrides — minimatch 11 под Stryker» на
       поправку ведущей к показу: с вариантом (а) аудит — 3, а не 0. Находка `minimatch` 10.2.6
       (`>=7.0.0 <11.0.1`) стоит в двух копиях — под `typescript-estree` и под
       `@stryker-mutator/core` 10.1.1, последним выпуском с `~10.2.4`; разбивка «7 + 4» в показе
       неверна (находка второй проверяющей 2026-11-27T10:58Z, аудит ведущей 10:59Z). Вторая
       подстановка — `overrides` `minimatch` 11 под `@stryker-mutator/core`: свежайшая версия не
       моложе 14 суток на день кода (2026-11-27 — 11.0.8). До неё проба: гейт порч TS на тех же
       файлах считает как без подстановки. Не прошла — подстановки нет: три записи (`minimatch`,
       core, vitest-runner) — в снимке с причиной до выпуска Stryker, цель F51 и строки приёмки —
       «до 3», тем же словом. Обе подстановки снимаются, когда Stryker или `typed-rest-client`
       выпустят исправление. Подписанные спека и план не меняются; решение — в decisions. -->
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
