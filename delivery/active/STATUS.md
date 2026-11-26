# Active delivery status

- **slug:** contour-b1b
- **stack:** delivery@2.30, cqg@3.30, okf@absent
- **stack-selftest:** external (корень проекта, вне git: копии из ~/Documents/Prepare @ 212443d)
- **class:** L
  <!-- Класс — как у В1а: новые пакеты npm в dev-зависимостях, CI и хуки контура. Приёмка — вторая
       проверяющая (полоса третьей). -->
- **kind:** feature
- **repro_test:** n/a reason=не bugfix
- **diagnosis:** n/a reason=не bugfix
- **phase:** plan
  <!-- Полоса фронта — слово владельца 2026-11-26T08:53:28Z «да на всё» на список ведущей:
       «Схема» → В1б → «Клиент API и связь» → 4г. Черновика 19.11 у В1б нет: спека и план
       написаны исполнителем 2026-11-26 на main `b4fd595` по спеке В1а, объявлениям
       `scripts/lint/` и канону `cqg@3.30`. Черновики — ведущей на чтение; код — после подписи
       владельца: спека, план, шесть пакетов. -->
- **builder:** agent:front-session
- **verifier:** agent:reviewer2-session
- **human_ok_spec:** pending
- **human_ok_plan:** pending
  <!-- Строки `new_dependency` для шести пакетов — после слова владельца, с `by=human:owner`
       (L93). -->
- **shape-oracles:** cqg-deployed
  <!-- Половина Python — с В1а; TS-половина — эта поставка. -->
- **behavior-oracles:** tests-present — pytest: контракт CI и хуков, мета-гейт; Vitest: F17, правило З14, свойство fast-check; гейты на копиях с нарушением
- **artifact_oracle:** n/a reason=поставка ничего не собирает; оракул сборки фронта — Ч1.4а, его цепочка идёт в CI этой поставкой
- **ci-oracles:** deployed
- **worktree:** .claude/worktrees/smoke-run
  <!-- Поставка в копии smoke (smoke, Ч1.12а, Ч1.14а1, «Схема», В1б): новую копию в общем
       `.claude/worktrees/` изолированная сессия исполнителя не заводит; копия — решение
       ведущей. -->
- **parallel_sessions:** manual-deploy
- **hooks:** not-deployed
- **blockers:** none
- **waivers:** none
- **runtime_paths:** .github/workflows/
  <!-- Как у В1а: джобу CI видит только прогон CI. -->
- **irreversible_surfaces:** публикация репозитория (публичный GitHub — создание и push), выкат на общий сервер, любая трата на платных сервисах, выпуск отчёта клиенту
  <!-- Строка — как в `delivery/CONSTITUTION.md` и STATUS «Схемы». Подпись объявления — окном
       `scripts/sign_irreversible.sh` касанием владельца, по поставке, после слова на спеку. -->
- **model_surface:** n/a reason=модель продукт не вызывает; поверхность объявляется с первым вызовом модели — волна В3 (§14)
- **rule_enforcers:** n/a reason=model_surface не объявлена
- **canon_drift_waiver:** no
- **baseline_growth_waiver:** no
- **observability:** 1
- **observe_signal:** первые запросы и push в main после слияния — `quality` зелёный: цепочка фронта идёт, у каждой TS-половины «просмотрено N» > 0, доктор — DEAD 0; первая поставка фронта после В1б проходит под CI без чистого клона ведущей «рукой»
- **observe_until:** pending — назначается на сдаче
- **circuit_breakers:** max_loc_diff=2500 max_files_touched=50
  <!-- Предел 2500 строк вместе с тестами и 50 файлов — слова владельца 2026-11-19T14:26Z и
       14:36Z; порог «сказать до сдачи» — 2300. -->
