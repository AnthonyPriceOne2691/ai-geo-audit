# Eval smoke — Ч1.5в «Остановка и боевое наложение»

Derived from spec acceptance. Run during verify. Живой оракул стека — на машине исполнителя на
`1801f00` (код и тесты — `b8bcb17`), 2026-11-24T12:10:53–12:14:12Z: шагов 28, красных 0, своего
не осталось; строки — в отчёте, «Исполнение рисковых путей». Живые порчи A88 — временная
локальная ветка `cafd26c` поверх `aa8ce6b`, 11:43:17–11:47:25Z: шагов 28, красных 4; ветка снята.
CI — прогон 45386808177 на `aa8ce6b`, 10 из 10; `1801f00` выложен 12:11:14Z, итог CI — у
ведущей. Набор тестов на `1801f00` — 1243 из 1243 под `-W error`. Сквозные проверки
`delivery/evals/smoke/run.sh` на `1801f00`, 2026-11-24T12:15:12–12:16:27Z: «сценариев 3 из 3» —
S3, S1 и S2 зелёные. Порчи — отчёт, «Behavior oracles»; пробы проверяющей — её разделы отчёта.

- [x] A87 — счёт «сервисов 5, правил 24»: шестнадцать кодов Ч1.5а, семь A87 и `stop-grace` A88.
      Тесты `test_a73_a87_a88_rule_codes_are_a71_a72_a87_and_stop_grace_in_order`,
      `test_a73_a87_summary_names_services_and_rules`,
      `test_a73_a87_rules_step_wants_five_services_and_twenty_four_rules`; шаг «A73 A87 правила
      на настоящем docker-compose.yml» — «итог: сервисов 5, правил 24, нарушений 0; config
      --services — 5» (12:11:07Z)
- [x] A88, первая строка — шаг «A88 остановка api»: «exited с кодом 143 за 0.3 с; новая запись
      uvicorn; разбор — «остановлен»» (12:13:57Z); перезапусков 0 и контейнер внизу — условия
      шага. Живая порча `stop_signal: SIGKILL` — «КРАСНОЕ: exited, код 137; разбор — «убит по
      сроку»» с хвостом журнала api (11:46:19Z). Тесты
      `test_a88_stop_is_exited_143_fast_judged_stopped_and_stays_down`,
      `test_a88_uvicorn_records_of_our_format_are_counted`,
      `test_a88_stop_step_is_green_with_a_new_record_and_returns_api`,
      `test_a88_stop_step_red_names_the_api_log_taken_before_the_restart`
- [x] A88, вторая строка — разбор `ai_geo_audit.ops.stop_verdict`: случаи строки примера —
      `test_a88_stop_is_judged_by_code_record_and_command`,
      `test_a88_verdicts_read_as_the_example_names_them`; свойство «ровно один вердикт из трёх» —
      `test_a88_any_input_gets_exactly_one_of_three_verdicts`. Оракул спрашивает вердикт у пакета
      в собранном образе: `test_a88_verdict_is_asked_of_the_package_in_the_built_image`,
      `test_a88_verdict_script_answers_with_the_package_words`
- [x] A88, третья строка — правило `stop-grace`: api 15 с и 29 с, db 30 с и 59 с — красное
      (`test_a88_short_stop_grace_is_red_for_its_service`); 30 с и 60 с — не нарушение
      (`test_a88_stop_grace_at_or_above_the_limit_is_not_a_violation`); предел api — из
      `server_settings()` (`test_a88_api_limit_is_read_from_server_settings`); db судится и без
      api (`test_a88_db_term_is_judged_without_api`); строка называет срок и нужное
      (`test_a88_stop_grace_line_names_the_term_and_the_need`); срок — в секундах, незнакомый вид
      — отказ (`test_a88_duration_is_read_in_seconds`). Живая порча `stop_grace_period: 15s` —
      «stop-grace: сервис api — stop_grace_period 15 с, нужно не меньше 30 с» в A73 и A89
- [x] A88, четвёртая строка — шаг «A88 падение uvicorn изнутри контейнера»: «убит pid 7 —
      дочерний процесс init; перезапусков +1; healthy через 5.8 с; сеть — 200» (12:14:04Z). Живая
      порча `restart: "no"` — «КРАСНОЕ: api не перезапущен за 50 с» с хвостом журнала api
      (11:47:16Z). Тесты
      `test_a88_crash_is_one_restart_healthy_within_50_s_and_heard_from_the_network`,
      `test_a88_uvicorn_must_be_the_one_child_of_init`,
      `test_a88_search_takes_only_uvicorn_by_argv_words`,
      `test_a88_crash_step_kills_the_child_of_init_with_sigkill_and_returns_api`,
      `test_a88_crash_step_with_a_soft_exit_is_red`,
      `test_a88_crash_step_without_a_child_of_init_or_a_restart_is_red`
- [x] A89, первая строка — `test_a89_each_prod_rule_has_its_probe`: 16 проб, у каждой — ровно
      своё нарушение с кодом правила и именем сервиса; без правила проба проходит, и тест
      красный. Соседние случаи — `test_a89_neighbour_cases_are_not_violations`; строки нарушений
      целиком — `test_a89_violation_lines_name_the_rule_and_the_service`; значение ссылкой и
      команда без слов — отказ строкой целиком
      (`test_a89_value_unknown_until_the_server_is_refused`). Порчи копии наложения у
      проверяющей — ровно своё нарушение
- [x] A89, вторая строка — шаг «A89 правила на основном файле с боевым наложением»: «итог:
      сервисов 5, правил 27, нарушений 0; config --services — 5» (12:11:07Z). Шаг зовёт
      `python -m ai_geo_audit.ops.stack_rules --prod` в образе — тот же вход у
      `test_a89_python_m_entry_as_in_the_image` (находка 4: отказ — код 2 строкой, верный вывод —
      «правил 27»). Ещё `test_a89_prod_rules_run_only_under_the_flag`,
      `test_a89_prod_rules_step_reads_both_files_with_the_flag`. Живая порча основного файла —
      A89 красный с двумя нарушениями, `restart` и `stop-grace` (11:43:30Z)
- [x] A23 — сканер правила LOGIC-10 (рабочий инструмент сессии, вне репозитория) по новым и
      изменённым файлам поставки против main `aefde68`: на `1801f00` файлов 25, совпадений 4, все
      — текст самого правила: строки 52, 53 и 86 `delivery/CONSTITUTION.md` (файл в диффе строкой
      о тратах, коммит ведущей `76140c1`) и строка A23 спеки (`spec.md:97`). Документы T6 до
      коммита — 0 совпадений. Повтор — на коммите сдачи
