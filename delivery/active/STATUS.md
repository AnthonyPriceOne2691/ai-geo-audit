# Active delivery status
- **slug:** ch1-06a1-fix-mutants-root
- **stack:** delivery@2.30, cqg@3.30, okf@absent
- **stack-selftest:** external (тексты канона в корне рабочей копии, вне git; ревизия 212443d)
- **class:** S
- **kind:** bugfix
- **repro_test:** backend/tests/test_mutants_layout.py::test_deploy_fakes_load_script_from_mutants_layout
- **diagnosis:** active/diagnosis.md
- **phase:** verify
- **builder:** agent:hands-session
- **verifier:** agent:lead-session
- **human_ok_spec:** yes (by=human:owner at=2026-11-23T10:15Z)
  <!-- Слово владельца «да на всё, подписываю оракул» 2026-11-23T10:15Z на список ведущей,
       п. 1: правка тестов ядра выката — опубликовать по готовности. Мини-спека — в tasks.md,
       тест-повтор — `test_mutants_layout.py`. Вносит проверяющий. -->
- **human_ok_plan:** n/a
- **shape-oracles:** cqg-deployed
- **behavior-oracles:** tests-present — pytest; тест-повтор кладёт копию помощников тестов выката в раскладку mutmut и грузит скрипт выката в подпроцессе
- **artifact_oracle:** n/a reason=поставка ничего не собирает: правка помощника тестов
- **ci-oracles:** deployed
- **worktree:** .claude/worktrees/ch1-06a1-fix
- **parallel_sessions:** manual-deploy
- **hooks:** not-deployed
- **blockers:** none
- **waivers:** none
- **runtime_paths:** none reason=правка помощника тестов; путей, чей отказ не виден тестам, поставка не трогает
- **irreversible_surfaces:** публикация репозитория (публичный GitHub — создание и push), выкат на общий сервер, трата на платных сервисах выше $5 за одно действие, выпуск отчёта клиенту
- **model_surface:** n/a reason=модель продукт не вызывает; поверхность объявляется с первым вызовом модели — волна В3 (§14)
- **rule_enforcers:** n/a reason=model_surface не объявлена
- **canon_drift_waiver:** no
- **baseline_growth_waiver:** no
- **observability:** 1
- **observe_signal:** <заполняется на handoff>
- **observe_until:** <заполняется на handoff>
- **circuit_breakers:** max_loc_diff=2500 max_files_touched=50
