# Active delivery status

- **slug:** contour-b0
- **stack:** delivery@2.21, cqg@absent, okf@absent
  <!-- Тексты канонов одной ревизии: delivery@2.21 · cqg@3.14 · okf@1.24 · stack-map@1.57.
       Развёрнута только ось ① (волна В0). ② — волна В1, ③ — волна В2. -->
- **stack-selftest:** external (корень проекта, вне git: копии из ~/Documents/Prepare @ 5d82ab1)
- **class:** S
- **kind:** bootstrap
- **repro_test:** n/a reason=не bugfix
- **diagnosis:** n/a reason=не bugfix
- **phase:** verify
- **builder:** agent:hands-session
- **verifier:** agent:lead-session
- **human_ok_spec:** n/a reason=класс S, §2.2 — достаточно tasks
- **human_ok_plan:** n/a reason=класс S
- **shape-oracles:** weak
  <!-- CQG не развёрнут: волна В1, приходит, когда каркас первой части лежит в main;
       крайний срок — до начала второй части и до первого платного вызова.
       Дёшево и не сделано: ruff, pre-commit и ратчет снимков ставятся одной волной,
       иначе снимок снимается дважды. -->
- **behavior-oracles:** weak — кода продукта нет; поведенческие тесты придут с первой поставкой каркаса; дёшево и не сделано: прогон `scripts/delivery_check.py` в CI (CI — волна В1)
- **artifact_oracle:** n/a reason=проект пока ничего не собирает; оракул на собранное придёт с первым образом Docker и с первым отчётом PDF/XLS
- **ci-oracles:** weak
  <!-- CI не развёрнут (волна В1). Класс S — waiver не требуется. Вместо CI — прогон
       гейта в чистом клоне в verify-report.md. -->
- **worktree:** none (S)
- **parallel_sessions:** manual-deploy
  <!-- Две сессии (исполнитель и проверяющий) и субагенты исполнителя в одном рабочем
       дереве. Выкат на общий сервер — только отдельным словом владельца (manual-deploy).
       Интеграция в main до волны В1 держится процессом, а не механизмом: в main идёт
       только принятое проверяющим, перемоткой ветки после «принято». merge_guard.sh
       придёт с CQG в В1 — тогда значение станет merge-guard,manual-deploy. Коммит —
       только по путям; у проверяющей сессии своя история документов вне этого git. -->
- **hooks:** not-deployed
- **blockers:** none
- **waivers:** none
- **new_dependency:** none
- **runtime_paths:** none reason=кода продукта нет; первыми придут миграции с нуля и восстановление бэкапа на чистой базе (каркас), общий лимит денег и живые провайдеры (первый платный клиент), сборка PDF (отчёты)
- **irreversible_surfaces:** публикация репозитория (публичный GitHub — создание и push), выкат на общий сервер, трата на платных сервисах выше $5 за одно действие, выпуск отчёта клиенту
- **model_surface:** n/a reason=кода нет, модель продукт не вызывает; поверхность объявляется в поставке с первым вызовом модели — волна В3 (§14)
- **rule_enforcers:** n/a reason=model_surface не объявлена
- **canon_drift_waiver:** no
- **baseline_growth_waiver:** no
- **observability:** 1
- **observe_signal:** развёрнутый контур отклоняет: `delivery_check.py` краснеет на STATUS без фазы и на разошедшихся правах; `extract_payload.py --check-local .` краснеет на тексте канона без строки игнора
- **observe_until:** 2026-12-02
- **circuit_breakers:** defaults from AGENT_DELIVERY_HARNESS.md §3.4

## Названные остатки (вариант D)

- `git add -f` обходит `.git/info/exclude`. Закрыто запретом в правах агента и
  правилом конституции; механически на публичном репозитории закрыть нечем.
- Строки `.git/info/exclude` в новый клон не приезжают. Хук-сторож текстов
  (AGENT_STACK §7.1a) — рецепт для `.pre-commit-config.yaml`, а pre-commit придёт
  волной В1. До неё вариант D держат строки игнора в этом клоне, вопрос на каждый
  `git push` в правах агента и проверка перед push: `git ls-files` без имён текстов
  канона и два нуля `--check-local`.
- `stack-selftest: external` — шаг «Canon payload selftest» в CI проекта невозможен:
  текстов канонов там нет по построению. Самопроверка канонов гоняется там, где они
  лежат, при каждой их правке.
