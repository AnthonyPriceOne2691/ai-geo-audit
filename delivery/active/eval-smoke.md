# Eval smoke — «Учётки-2» «Пароль очереди, правила и поиск секретов»

Derived from spec acceptance. Run during verify. Живой оракул стека — на машине исполнителя на
`68fbc32` (код среза 3), 2026-11-25T13:56:39–14:00:36Z, и на `f3e8320` (код среза 4: правила
`db-roles` и `redis-auth`), 14:40:00–14:43:59Z: оба раза шагов 38, красных 0, своего не осталось;
на `38d7be0` (срез 5: A173 и четыре дыры правил), 14:57:57–15:01:40Z: шагов 39, красных 0. Строки
A171 и A179 — ниже, из прогона на `f3e8320`; у A171 слова те же, что на `68fbc32`, кроме времени
остановки (0,2 с и 0,3 с); A173 — из прогона на `38d7be0`. Порчи живьём на своём redis и стеке — у
третьей проверяющей на `ee29457` (сообщение 14:38:41Z): нет `user:`, нет `exec`, проверка здоровья
Ч1.5а, снята `requirepass`, пароль аргументом, один `$` — красные. Остаток среза 5 — A172 (первая
строка) и A380 (первая строка) — завтра здесь же (решение ведущей 14:58:46Z, мера — до 2400).
Полная сверка — T4.

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
      `test_a171_redis_steps_come_last_in_the_oracle_table`
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
      `test_a177_a178_lines_name_service_and_variable_without_values`
- [x] A178 — `test_a178_each_redis_auth_condition_has_its_probe` — проба на условие,
      `test_a178_queue_password_at_a_process_is_caught_by_both_rules`,
      `test_a178_neighbour_cases_are_not_violations`, строки без значений — в тесте A177, A178
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
      `test_a173_step_is_in_the_table_before_the_breaking_step`
- [x] A380, вторая строка — `test_a380_script_revokes_members_and_rights_with_grantor_and_cascade`,
      `test_a380_spoiled_command_is_red_with_its_script_line`, `test_a380_missing_command_is_red`
      (проверка описания, а не исполнения, §12.6)
