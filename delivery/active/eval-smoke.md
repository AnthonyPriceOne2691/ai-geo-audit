# Eval smoke — Ч1.14а1 «Исходящие»

Derived from spec acceptance. Run during verify. Числа прогонов — `verify-report.md`; здесь — чем
держится каждый пример. Тесты — на Redis и Postgres стека разработки, в CI — на службах джобы.

- [x] A240 — постановка (`test_alerts_queue.py`):
      - запись по форме, в начале списка, секрет — маской:
        `test_a240_alert_lands_first_as_one_ascii_json_line`; до 32 КиБ —
        `test_a240_longest_record_fits_32_kib`;
      - предел и счётчик: `test_a240_full_outbox_keeps_1000_and_counts_the_dropped`; отброс больше
        одного — `test_a240_overlong_outbox_counts_every_dropped` (1 005 → 1 000, счётчик 6);
      - закрытый порт, `OOM`, `NOAUTH`:
        `test_a240_refusals_are_one_error_line_in_words_and_false`; сбой вне перечня —
        `test_a240_unforeseen_failure_is_words_and_false`;
      - молчащий — не позже 2,5 с, одно соединение, после срока не ставится:
        `test_a240_silent_redis_is_cut_at_two_seconds_once`; замерший или оборвавший соединение
        посреди круга — `test_a240_lost_reply_is_false_in_time_and_never_repeated` (4 случая);
        поток, получивший процессор после срока, —
        `test_a240_thread_that_starts_after_the_deadline_queues_nothing`; имя, которое не
        разрешается, — `test_a240_name_that_does_not_resolve_is_cut_in_time`;
      - чужая правка списка между `LLEN` и `EXEC`:
        `test_a240_watch_conflict_is_retried_and_queued_once`; вызов из работающего цикла —
        `test_a240_call_from_a_running_loop_is_false_in_words`
- [x] A241, первая строка — текст (`test_alerts_text.py`):
      - шапка, время, строки, совет: `test_a241_text_is_header_time_body_and_todo`; пояс процесса —
        `test_a241_time_is_utc_in_any_process_zone`;
      - длина и форма слова: `test_a241_long_body_loses_lines_from_the_end` (4 случая),
        `test_a241_more_lines_takes_the_form_by_number` (13);
      - обрезка частей: `test_a241_long_parts_are_cut_before_counting`; ровно в предел —
        `test_a241_part_exactly_at_its_limit_is_whole` и
        `test_a241_text_exactly_at_the_limit_keeps_its_lines`;
      - одинокий суррогат — `test_a241_lone_surrogate_is_replaced_and_the_rest_is_whole`
- [x] A241, вторая строка — маска секретов настроек:
      `test_a241_setting_secret_in_a_line_becomes_the_mask`
- [x] A242 — команда `check` (`test_alerts_commands.py`):
      `test_a242_check_queues_one_alert_after_logs_and_hooks`,
      `test_a242_check_without_redis_is_code_1_and_one_reason`,
      `test_a242_other_arguments_are_the_usage_line`,
      `test_a242_bad_setting_at_import_is_words_without_traceback`,
      `test_a242_guarded_paths_do_not_leak_the_setting_secret`
- [x] A243 — здоровье и Redis (`test_api_health.py`):
      `test_a243_health_sees_redis_by_a_probe_write`,
      `test_a243_redis_refusal_is_unavailable_with_one_record` (закрытый порт, пароль, полный),
      `test_a243_silent_redis_is_cut_by_the_limit`,
      `test_a243_database_and_redis_down_is_one_answer_and_two_records`,
      `test_a243_paths_without_a_pass_do_not_touch_redis`. В стеке — живой оракул A65 и A69
- [x] A23 — сканер правила LOGIC-10 (рабочий инструмент сессии, вне репозитория): отчёт, «Product
      oracles»
