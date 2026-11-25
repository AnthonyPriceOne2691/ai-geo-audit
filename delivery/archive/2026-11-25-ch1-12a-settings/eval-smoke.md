# Eval smoke — Ч1.12а «Настройки — таблица и чтение»

Derived from spec acceptance. Run during verify. Живой оракул стека — на машине исполнителя на
`8c813b0` (код — как на `c8bbef3`, кроме журнала api в причине A65), 2026-11-25T09:14:17–09:17:36Z:
шагов 28, красных 0, своего не осталось. Живая порча записи мастер-ключа (L126) — временная
локальная ветка поверх `966f910`, 09:26:20–09:26:43Z: шагов 28, красных 1 — A65; ветка
снята. CI — прогон 45587112792 на `c8bbef3`, зелёный целиком; порчи — убито 716 из 742 (96%).
Набор на `966f910` — 1447 из 1447 под `-W error` (09:28:52–09:30:16Z); на
`c8bbef3` изменённые файлы тестов — 106 из 106, весь набор — в чистом клоне. Сквозные проверки
`delivery/evals/smoke/run.sh` на `c8bbef3`, 09:36:48–09:38:21Z: «сценариев 3 из 3» — S3 за 0 с, S1
за 1 с, S2 за 91 с; S3 сверяет и таблицы `settings/` (L121). Порчи — отчёт, «Behavior oracles».

- [x] A220, первая строка — разбор файла `config.master_key.parse`: ключей 1, 2 и 1, заглавные с
      CRLF — тот же ключ; негодные — `MasterKeyError` с именем, номером строки и причиной, без
      содержимого ни в тексте, ни в цепочке. Тесты `test_a220_good_files_give_their_keys_in_order`,
      `test_a220_upper_case_with_crlf_is_the_same_key`, `test_a220_bad_file_is_refused_with_line_and_reason`
      (19 случаев), `test_a220_unset_folder_and_closed_file_are_refused_in_words`,
      `test_a220_file_is_read_once_per_process`. Оракул пишет ключ этого формата —
      `test_a65_a220_oracle_master_key_passes_the_startup_parse`; живой A65 на `8c813b0` — «ок»,
      порча 62 знака hex — A65 красный
- [x] A220, вторая строка — условие: тест-договор кладёт поставка, что сливается второй; 6а-2 с
      `env-init` в main нет (`8adca75`), порядок слияния — Ч1.12а первой (решение ведущей
      2026-11-25T09:20:15Z): строка здесь не исполняется, тест кладёт 6а-2 (decisions)
- [x] A221 — проверка старта в бою у api и worker: `test_a221_prod_api_and_worker_refuse_a_bad_master_key_file`
      (11 случаев), `test_a221_good_master_key_file_gives_no_reason`,
      `test_a221_closed_file_and_folder_are_reasons_in_words`. Живой A65 — api стартовал с
      мастер-ключом нового формата; с негодным — не стартовал (порча L126)
- [x] A222 — шифр: `test_a222_two_ciphers_differ_and_hide_the_value`,
      `test_a222_foreign_name_and_changed_sign_are_refused`,
      `test_a222_good_token_with_no_pair_inside_is_refused`,
      `test_a222_token_with_broken_utf8_inside_is_refused`; строка A222 — по слову владельца
      2026-11-24T14:27:45Z
- [x] A223 — две строки файла: `test_a223_previous_line_opens_and_new_records_use_the_first`; с
      базой — `test_a223_gone_previous_line_is_named_with_an_error_record`: `KeyUnreadable` с номером
      мастер-ключа и запись ERROR `key-unreadable` с полями и текстом целиком (находка 18)
- [x] A224 — `test_a224_rows_hold_only_ciphertext_and_put_key_does_not_commit`,
      `test_a224_master_id_and_fingerprint_are_hex_of_fixed_length`,
      `test_a224_fingerprint_is_keyed_by_name_value_and_current_master`,
      `test_a224_hint_is_four_last_signs_of_a_long_value`, `test_a224_known_answers_pin_the_record_format`,
      `test_a224_key_row_repr_shows_the_name_and_master_id_only` (находка 9)
- [x] A225 — `test_a225_bad_key_is_refused_before_any_write`,
      `test_a225_neighbours_of_the_refusals_are_written`,
      `test_a225_unknown_name_is_refused_by_drop_and_resolve`. Строже показанного: `\r` — перевод
      строки и по краям, и внутри; `\x0b` и `\x0c` — управляющие знаки (решение ведущей: не правка
      подписанного текста)
- [x] A226 — `test_a226_same_value_keeps_the_row_and_drop_names_its_outcome`: итог записи равен
      строке `states()` при «задан» и «без изменений» (находка 17)
- [x] A227 — `test_a227_resolve_sees_each_change_on_the_next_call`
- [x] A228 — `test_a228_resolved_value_is_masked_in_the_log` (json и текст),
      `test_a228_every_secret_form_is_masked_in_the_log`
- [x] A229 — `test_a229_the_package_keeps_the_rule` (0 нарушений, модулей 37),
      `test_a229_no_modules_is_a_refusal`, `test_a229_violations_of_every_module_are_kept`,
      `test_a229_each_probe_is_red_with_its_path_and_line` — 21 нарушение и 4 соседа, в том числе
      обходы и находки 15, 16 и 19 второй проверяющей. Красное на копии `src` с нарушением в `api/`
      — «модулей просмотрено 38», с путём и строкой
- [x] A230 — `test_a230_key_registry_is_the_table`,
      `test_a230_states_follow_the_registry_without_values`,
      `test_a230_swapped_rows_are_refused_with_the_key_word`
- [x] A231 — `test_a231_two_writes_at_once_leave_one_row_of_the_later_commit`
- [x] A232 — `test_a232_setting_registry_is_the_table`,
      `test_a232_each_change_is_a_new_version_and_a_repeat_writes_nothing` (с `expected=0` при
      версии 2 — находка 10), `test_a232_default_is_not_written_and_true_in_the_base_is_not_one`
- [x] A233 — `test_a233_bad_parallel_is_refused_before_any_write` (5 случаев),
      `test_a233_bounds_are_written_and_an_unknown_name_is_refused`
- [x] A234 — `test_a234_two_writes_of_one_version_at_once_leave_one` (10 прогонов подряд у
      исполнителя, 20 из 20 у второй проверяющей),
      `test_a234_another_constraint_is_not_taken_for_a_changed_setting`,
      `test_a234_an_error_without_a_constraint_name_is_named_none`
- [x] A235 — `test_a235_bad_value_in_the_base_is_refused_loudly` (6 случаев): текст отказа,
      запись ERROR `setting-invalid` без значения, исключение в записи — `Unfit` (находка 12)
- [x] A236 — `test_a236_tables_come_with_the_revision_and_go_with_its_downgrade`,
      `test_a236_raw_key_insert_is_refused_by_its_own_constraint`,
      `test_a236_raw_setting_insert_is_refused_by_its_own_constraint`,
      `test_a236_model_checks_mirror_the_database`; живой migrate оракула — `0001 → 0002`
- [x] A237 — метки значений и строк файла нет в отказах, цепочках и журнале: `leaks.traces` и
      `leaks.links` в тестах A220, A222, A225, A227 и A228 (докстринги называют A237)
- [x] A238 — `test_a238_codes_are_brought_to_case_and_a_case_repeat_writes_nothing` (рядом
      `audits_parallel` — находка 11), `test_a238_three_hundred_pairs_are_written`,
      `test_a238_bad_locales_are_refused_before_any_write` (13 случаев),
      `test_a238_lookalike_letters_are_refused_before_the_case_is_brought` (14 случаев: семь
      кодов на обоих местах пары; вторая проверяющая перебрала все кодовые точки — проходят ровно
      52 буквы ASCII)
- [x] A23 — сканер правила LOGIC-10 (рабочий инструмент сессии, вне репозитория) по новым и
      изменённым файлам против main `8adca75`: на `c8bbef3` файлов 48, совпадений 0. Повтор — на
      коммите сдачи
