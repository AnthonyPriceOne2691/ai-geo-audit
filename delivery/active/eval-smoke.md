# Eval smoke — «Учётки-2» «Пароль очереди, правила и поиск секретов»

Derived from spec acceptance. Run during verify. Живой оракул стека — на машине исполнителя на
`68fbc32` (код среза 3), 2026-11-25T13:56:39–14:00:36Z, и на `f3e8320` (код среза 4: правила
`db-roles` и `redis-auth`), 14:40:00–14:43:59Z: оба раза шагов 38, красных 0, своего не осталось;
на `38d7be0` (срез 5: A173 и четыре дыры правил), 14:57:57–15:01:40Z: шагов 39, красных 0; на
`2f29616` (срез 5 целиком: A380 и A172, первые строки), 2026-11-26T08:39:27Z–08:43:08Z: шагов 42,
красных 0, своего не осталось. Строки A171 и A179 — ниже, из прогона на `f3e8320`; у A171 слова те
же, что на `68fbc32`, кроме времени остановки (0,2 с и 0,3 с); A173 — из прогона на `38d7be0`;
A380 и A172 — на `2f29616`. Порчи живьём на своём redis и стеке — у третьей проверяющей на
`ee29457` (сообщение 14:38:41Z): нет `user:`, нет `exec`, проверка здоровья Ч1.5а, снята
`requirepass`, пароль аргументом, один `$` — красные; живой круг на `b8485d8` — без блокирующих
(сообщение ведущей 2026-11-26T08:23Z). Остаток среза 5 — A172 и A380, первые строки, — сделан
2026-11-26 (решение ведущей 2026-11-25T14:58:46Z; мера 2463 — по её слову 08:38:09Z). Полная
сверка — T4.

- [x] A171, первая строка — шаг «A171 очередь: без пароля и с неверным — отказ, с верным — PONG»:
      «без пароля — NOAUTH; неверный — WRONGPASS в stderr, PONG нет; верный — PONG» (14:43:56Z).
      Тесты `test_a171_auth_step_without_wrong_and_right_password`,
      `test_a171_auth_step_is_red_on_each_wrong_answer`,
      `test_a171_queue_password_goes_to_redis_and_the_api_queue_address`
- [x] A171, вторая строка — шаг «A171 redis: процесс, пользователь, вход и мягкая остановка»:
      «healthy, записи — PONG; пользователь redis; PID 1 — redis-server, uid 999, вход — канал;
      процессы — redis-server *:6379; остановка — код 0 за 0.3 с» (14:43:57Z). Тесты
      `test_a171_process_step_line_and_commands`, `test_a171_process_step_is_red_on_each_branch`,
      `test_a171_slow_stop_is_red`, `test_a171_top_commands_take_the_cmd_column_whole`,
      `test_a171_redis_steps_come_last_before_the_final_search`
- [x] A175 — тесты `test_a175_short_queue_password_is_refused_counted_after_decoding`,
      `test_a175_unencoded_sign_in_the_queue_password_is_refused`,
      `test_a175_queue_password_of_12_signs_and_good_urls_start`,
      `test_a175_queue_placeholder_is_one_reason`, `test_a175_migrate_does_not_check_the_queue`
- [x] A176, первая строка — `test_a176_queue_password_is_scrubbed_in_both_forms` (держится с Ч1.2в)
- [x] A176, вторая строка — `test_a176_server_block_has_placeholders_and_process_roles`,
      `test_a176_real_password_and_migrate_role_in_api_url_are_caught`
- [x] A177 — `test_a177_each_db_roles_condition_has_its_probe` — проба на условие,
      `test_a177_db_password_at_a_process_is_caught_by_both_rules`,
      `test_a177_neighbour_cases_are_not_violations`,
      `test_a177_a178_lines_name_service_and_variable_without_values`,
      `test_a177_a178_each_reason_is_exact`
- [x] A178 — `test_a178_each_redis_auth_condition_has_its_probe` — проба на условие,
      `test_a178_queue_password_at_a_process_is_caught_by_both_rules`,
      `test_a178_neighbour_cases_are_not_violations`,
      `test_a178_health_off_is_judged_by_the_healthcheck_rule`; строки без значений — в
      тесте A177, A178
- [x] A179, первая строка — `test_a179_role_names_are_the_process_roles`,
      `test_a179_password_variables_are_the_rule_constants`,
      `test_a179_lock_key_and_wait_are_the_code_values`
- [x] A179, вторая строка — `test_a73_a87_a88_rule_codes_are_a71_a72_a87_and_stop_grace_in_order`
      (перечень 26 кодов поимённо), `test_a89_prod_codes_follow_the_main_set_in_order` (29)
- [x] A179, третья строка — шаг A73 на настоящем основном файле: «итог: сервисов 5, правил 26,
      нарушений 0» (14:40:17Z), с боевым наложением и `--prod` — «итог: сервисов 5, правил 29,
      нарушений 0» (14:40:18Z). Тесты
      `test_a73_a87_a179_rules_step_wants_five_services_and_twenty_six_rules`,
      `test_a89_prod_rules_step_reads_both_files_with_the_flag`
- [x] A173 — шаг «A173 отказ старта: чужая роль базы и пароль очереди»: «отказ старта: migrate —
      DATABASE_URL, код 1; api — DATABASE_URL, код 1; api — REDIS_URL, код 1; адресов, паролей и
      Traceback нет; ревизия прежняя» (15:01:29Z). Тесты
      `test_a173_step_runs_three_refusals_and_keeps_the_revision`,
      `test_a173_step_is_red_on_each_branch`,
      `test_a173_two_problems_of_one_container_are_both_named`, `test_a173_changed_revision_is_red`,
      `test_a173_step_is_in_the_table_before_the_breaking_step`,
      `test_a173_limit_names_the_case` (предел — с сервисом и переменной)
- [x] A380, вторая строка — `test_a380_script_revokes_members_and_rights_with_grantor_and_cascade`,
      `test_a380_spoiled_command_is_red_with_its_script_line`, `test_a380_missing_command_is_red`
      (проверка описания, а не исполнения, §12.6)
- [x] A380, первая строка — шаг «A380 лишнее с передачей снимается, временная роль удалена»: «скрипт
      ролей — код 0; членств 0; у роли сервиса на alembic_version — DELETE, INSERT, SELECT, UPDATE
      без права передачи, у PUBLIC — нет; oracle_grantor удалена» (08:42:51Z). Тесты
      `test_a380_passed_extra_is_revoked_and_the_grantor_is_dropped`,
      `test_a380_extra_is_passed_on_by_set_role_and_from_a_temporary_grantor`,
      `test_a380_step_is_red_with_its_reason` (порчи «без `CASCADE`» и «без `GRANTED BY`»),
      `test_a380_grantor_is_dropped_when_making_the_extra_fails`
- [x] A172, первая строка — два шага «A172 поиск секретов»: на живом стеке перед A67 — «текстов 484:
      сырой вывод 472, журналы 5, описания 5, процессы 1, config 1; секретов 12, совпадений 0»
      (08:42:57Z); после всех шагов — «текстов 515: сырой вывод 503, журналы 5, описания 5,
      процессы 1, config 1; секретов 12, совпадений 0» (08:43:07Z). Тесты
      `test_a172_search_counts_texts_and_finds_nothing`,
      `test_a172_secret_in_any_text_or_no_texts_is_red` (порчи «в строке `requirepass` один `$`» и
      «migrate печатает свой адрес»), `test_a172_search_runs_on_the_live_stack_and_after_all_steps`
- [x] A23 — скан LOGIC-10 по новым и изменённым файлам поставки (рабочий инструмент сессии, вне
      репозитория): на `0013506` против `58de814` — добавленных строк 4447, совпадений 2, обе —
      строка A23 спеки (`spec.md`, текст самого правила); у ведущей на `4f2276f` против `b4fd595`
      — 0 из 4510 строк (её сообщение 2026-11-26T08:53Z). Повтор — на коммите сдачи (T5)
