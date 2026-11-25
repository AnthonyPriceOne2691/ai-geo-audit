# Eval smoke — «Учётки-1» «Роли базы и роль процесса»

Derived from spec acceptance. Run during verify. Живой оракул стека — на машине исполнителя на
`5f39992` (код среза 2), 2026-11-25T08:49:01–08:53:17Z: шагов 36, красных 0, своего не осталось;
повтор на `70134a1` (пин `libzstd1` из Ч1.12а), 08:53:59–08:57:30Z: шагов 36, красных 0, своего
не осталось; повтор на `85a900a` (находка 5: отзыв от настоящего выдавшего и с `CASCADE`),
09:05:02–09:08:33Z: шагов 36, красных 0, своего не осталось. Строки шагов ролей — ниже, из
прогона на `5f39992`; в обоих повторах они те же слово в слово. Порчи строк живьём — у третьей
проверяющей на `85a900a`, 2026-11-25T09:05–09:12Z, стек на порчу: красные 14 из 14, двенадцать —
там и так, как сказано в строке, две — иначе (ниже, у первой строки A85 и третьей A170;
decisions 09:19Z). Скан A23 и отчёт сверки — на verify (T3).

- [x] A85, первая строка — шаг «A85 роль сервиса: отказ на схему и alembic_version»:
      «current_user audit_app; команд 5 — у каждой 42501; alembic current = heads» (08:52:59Z).
      Тесты `test_a85_service_role_is_refused_every_command`,
      `test_a85_service_role_step_is_red_on_any_other_answer`,
      `test_a85_service_role_step_is_red_when_the_revision_moved`; наборы пробы —
      `test_a85_a170_probe_sets_are_the_example_commands`, виды транзакций —
      `test_a85_each_command_is_its_own_transaction_rolled_back`,
      `test_a85_together_set_is_one_transaction_rolled_back`,
      `test_a85_commit_set_keeps_its_work`; только SQLSTATE —
      `test_a85_error_text_is_not_printed`; вход `python -` —
      `test_a85_probe_from_python_stdin_prints_one_json_line`,
      `test_a85_main_in_process_prints_one_json_line`,
      `test_a85_probe_file_as_main_exits_with_the_code_of_main`,
      `test_a85_probe_file_is_self_contained`. Порчи живьём (третья, `85a900a`): «`CREATE` на
      `public`» — коды `['ok', 42501 × 4]`, красное на шаге; «роль сервиса — владелец базы» —
      красное раньше шага, на подъёме (A65): migrate — код выхода 1, в его журнале
      `permission denied for schema public`, api не стартует, до A85 не доходит (пробы третьей
      09:14Z и 09:19Z). Строка спеки ждёт «проходят `CREATE TABLE` и `CREATE SCHEMA`» — «было —
      станет» у ведущей
- [x] A85, вторая строка — шаг «A85 роль миграций: владелец базы, без особых прав»: «current_user
      audit_migrate; создание и удаление в транзакции с откатом — ok; владелец базы и
      alembic_version — audit_migrate; у обеих ролей вход, особых прав нет» (08:53:00Z); пароль
      роли миграций оракула — с «@», в адресе `%40`, migrate входит: «A65 A63 подъём стека и
      migrate» — ок (08:50:21Z). Тесты
      `test_a85_migrate_role_creates_in_a_rolled_back_transaction_and_owns_the_base`,
      `test_a85_migrate_role_step_names_each_wrong_row`,
      `test_a85_migrate_role_step_is_red_when_its_commands_fail`
- [x] A85, третья строка — шаг «A85 роль сервиса: строки — да, таблица — нет»: «у роли сервиса
      INSERT, SELECT, UPDATE, DELETE — ok, TRUNCATE и DROP — 42501; REFERENCES и TRIGGER — ложь;
      oracle_probe удалена» (08:53:02Z). Тесты
      `test_a85_service_role_works_with_rows_and_not_with_the_table`,
      `test_a85_service_rights_step_is_red_and_still_drops_the_table`,
      `test_a85_service_rights_step_stops_when_the_table_is_not_made`
- [x] A170, первая строка — шаг «A170 скрипт ролей второй раз: снимок прежний»: «скрипт ролей
      второй раз — код 0; снимок равен, строк 14; обе роли входят» (08:53:03Z). Тесты
      `test_a170_script_rerun_keeps_the_snapshot_and_both_roles_log_in`,
      `test_a170_rerun_step_is_red_on_code_snapshot_and_login`,
      `test_a170_probe_without_an_answer_is_red`
- [x] A170, вторая строка — шаг «A170 новый пароль роли сервиса и возврат»: «новый пароль роли
      сервиса: прежний адрес — 28P01, новый — вход; после запуска с прежним — прежний адрес снова
      входит» (08:53:04Z). Тесты
      `test_a170_new_service_password_refuses_the_old_address_and_comes_back`,
      `test_a170_new_password_step_is_red_with_its_reason`,
      `test_a170_new_password_step_returns_the_old_password_after_a_failed_probe`,
      `test_a170_new_password_step_returns_the_old_password_when_the_change_fails`,
      `test_a170_failed_login_gives_only_its_sqlstate`
- [x] A170, третья строка — шаг «A170 таблицы стенда до поставки — роли миграций»: «oracle_legacy
      и её последовательность — audit_migrate; роль сервиса читает и пишет; роль миграций удалила
      таблицу» (08:53:06Z). Тесты `test_a170_legacy_table_goes_to_the_migrate_role`,
      `test_a170_legacy_step_is_red_and_still_clears_the_table`,
      `test_a170_legacy_step_clears_the_table_after_a_failed_probe`,
      `test_a170_legacy_step_clears_the_table_when_making_it_fails`. Порча «таблицы
      суперпользователя не передаются роли миграций» живьём (третья, `85a900a`) — красное на
      владельцах: «oracle_legacy audit», «oracle_legacy_id_seq audit»; `DROP TABLE` у роли
      миграций проходит — она владелец базы, а схема `public` — у владельца базы;
      `ALTER TABLE … ADD COLUMN` у неё — `must be owner` (проба третьей 09:14Z). Строка спеки ждёт
      `DROP TABLE` — `42501` — «было — станет» у ведущей
- [x] A170, четвёртая строка — шаг «A170 лишнее у роли сервиса снимается»: «лишнее снято:
      TRUNCATE — 42501; CREATE на public и базу — нет; права по умолчанию — четыре и два; членств
      0» (08:53:08Z). Тесты `test_a170_extra_grants_are_revoked_by_the_script`,
      `test_a170_extra_step_is_red_and_still_drops_the_table`,
      `test_a170_extra_step_stops_when_the_table_is_not_made`
- [x] A172, вторая строка — шаг «A172 ошибка в скрипте ролей: откат, паролей в выводе нет»:
      «psql — код 3; роль миграций входит прежним паролем; паролей ролей нет в сыром выводе psql
      и в журнале db» (08:53:08Z). Тесты
      `test_a172_broken_script_rolls_back_and_shows_no_role_password`,
      `test_a172_broken_script_step_is_red_with_its_reason`,
      `test_a172_broken_script_step_needs_exactly_one_service_password_line`; сырой захват и маска
      — `test_a172_every_command_output_is_captured_raw_before_any_mask`,
      `test_a172_failed_command_reason_hides_invented_secrets`,
      `test_a172_timeout_keeps_what_the_command_printed`,
      `test_a172_run_puts_every_invented_secret_in_the_mask`,
      `test_a172_service_log_tail_hides_every_invented_secret`,
      `test_a172_step_lines_hide_invented_secrets`
- [x] A65 и A67 Ч1.5а — те же строки, вход строится иначе: «A65 A63 подъём стека и migrate» —
      ок (08:50:21Z), «A67 неверный пароль у migrate» — «up --wait — код 1; migrate — код выхода
      1; api — created» (08:53:16Z). Тесты `test_a65_migrate_and_api_addresses_carry_their_roles`,
      `test_a67_wrong_password_goes_only_to_the_migrate_role_address`; порядок шагов —
      `test_a85_a170_a172_role_steps_go_in_order_and_none_stops_the_rest`,
      `test_a88_stop_steps_go_after_hardening_and_before_a67`, набор номеров —
      `test_a76_steps_table_has_exactly_examples_a60_to_a67_a69_a73_a77_to_a89_a170_a172`
- [x] A174 — pytest `test_startup_checks.py`:
      `test_a174_foreign_db_role_is_refused_without_the_found_name`,
      `test_a174_own_db_role_starts`, `test_a174_role_name_is_compared_after_decoding`,
      `test_a174_password_in_the_name_place_is_short_and_not_printed`,
      `test_a174_one_reason_per_variable_role_goes_last`,
      `test_a174_outside_prod_a_foreign_role_is_silent`,
      `test_a174_process_roles_come_from_one_table`; срез 1 принят третьей проверяющей на `6335edf`
- [ ] A23 — скан LOGIC-10 по новым и изменённым файлам поставки: на verify (T3)
