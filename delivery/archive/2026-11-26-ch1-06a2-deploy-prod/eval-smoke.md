# Eval smoke — 6а-2 «Место токена, файл окружения, наложение и разбор конфига»

Derived from spec acceptance. Run during verify. Строки 6а-2 по `split.md`: A181 (первая и вторая),
A182, A189 (первая), A192, A23; A183 и A186 говорят «все команды» — их тесты растут на `env-init`,
`put-secret` и живые прогоны, строкой «держится с 6а-1». Живой оракул стека — на машине
исполнителя: на `bab74f1` (срез 2) и `b6354a1` (срез 4) — шагов 42, красных 0, A89 — «правил 31,
нарушений 0»; на `8a24203` (T5: шаг «A186 A189») — шагов 43, красных 1: находка шума `compose run`
в stderr, правка `951c63c`; на `951c63c` — шагов 43, красных 0, своего не осталось
(2026-11-26T13:21:34Z–13:25:12Z). CI: прогон 45818272612 на
`b8e6edf` — 10 из 10, живой шаг `deploy_live.py` 5 из 5 (джоба 139903541669); 45820076759 на
`e70f797` — 10 из 10; 45822960246 на `9b9b688` — 10 из 10 (`stack` 300 с); 45829442497 на
`951c63c` (код T5) — 10 из 10: в `stack` (джоба 139942038136) оракул — шагов 43, красных 0,
«A186 A189 … — ок» в 13:32:13Z; живой шаг — 6 из 6, `last` — «журнал второго env-init: строк 2,
выката нет, значений 0» (итог — от ведущей).

- [x] A181, первая строка — правила наложения на вымышленных конфигах:
      `test_a181_image_not_from_server_is_red_in_both_image_rules` (образ строкой, `${VAR}`,
      `${VAR:-…}`, `pull_policy` не `never` — у migrate, api и web),
      `test_a181_prod_mode_needs_literals_at_migrate_and_api`,
      `test_a89_each_prod_rule_has_its_probe` (проба на случай: без правила проба проходит — тест
      красный),
      `test_a89_violation_lines_name_the_rule_and_the_service`
- [x] A181, вторая строка — оракул, шаг A89 «правила на основном файле с боевым наложением»:
      «итог: сервисов 5, правил 31, нарушений 0» внутри собранного образа;
      `test_a181_image_digest_does_not_judge_own_server_images` (`${VAR:?…}` у migrate, api и web —
      не судится; порча «`image-digest` судит и `${VAR:?…}`» — три нарушения на настоящих файлах)
- [x] A182 — `env-init`, второй `env-init` и `put-secret` — тесты `test_a182_*` в
      `test_deploy_env.py` (22: файл по пометкам блока «сервер», права 600 root, мастер-ключ 400 uid
      api, пары «пароль — адрес», отказы и «ничего не тронуто»); живьём — шаг джобы `stack`
      `scripts/deploy_live.py` от root на корнях одноразовой машины: «env-init на чистых корнях»,
      «put-secret файла владельца», «второй env-init», «compose принимает файл сервера» — ок
      (прогон 45818272612); тесты шага — `test_a182_live_*`
- [x] A183 — держится с 6а-1, растёт на `env-init`, `put-secret` и `last`:
      `test_a183_env_init_and_put_secret_print_no_values`,
      `test_a183_no_mark_in_output_journal_or_arguments`,
      `test_a183_server_tree_ignores_env_and_journal_secrets_live_outside`,
      `test_a183_runner_values_only_through_environment`; живьём — «дерево сервиса чистое» и
      «last после хода» (T5: поздний журнал — второго `env-init`, «выката нет», значений 0) —
      тесты `test_a183_live_steps_in_order_last_after_the_moves`,
      `test_a183_live_last_shows_the_latest_journal_without_values`, `test_a183_live_last_red`;
      запуски в одну секунду — свои журналы, `last` — поздний:
      `test_runs_in_one_second_each_own_journal_and_last_shows_the_latest`
- [x] A186 — держится с 6а-1 (`test_deploy_core.py`, `test_deploy_snapshot.py`), растёт на
      `env-init` и `put-secret` — `test_a186_env_init_and_put_secret_write_only_under_own_roots`;
      живьём — шаг оракула «A186 A189»: снимок соседа ядром `deploy/deploy.py` против стека
      оракула на настоящем Docker — «снимок соседа: общих 5, перезапусков 0, коды прежние;
      пропало 0, новых 0» на `951c63c` (13:24:28Z); тесты шага —
      `test_a186_a189_snapshot_config_check_and_the_stack_untouched`, `test_a186_a189_step_red` (20
      видов), `test_a186_foreign_containers_of_the_machine_are_not_judged_by_the_step`,
      `test_a186_core_comes_from_the_copy_of_the_run`, `test_a186_core_missing_in_the_copy_is_red`,
      `test_a186_a189_step_on_the_live_stack_after_web_and_before_hardening`
- [x] A189, первая строка — `python -m ai_geo_audit.ops.config_check`: годный бой api и migrate —
      код 0 и «проверено имён: 19»; заглушка `JWT_SECRET` и короткий пароль базы — код 1, оба
      имени разом; `MIGRATION_LOCK_WAIT_SECONDS=abc` с `DB_POOL_SIZE=777` — отказ импорта
      подпроцессом настоящей командой, оба поля разом; ни `Traceback`, ни значения; сервис не
      стартует, в базу и Redis не ходит (страж аудита): `test_config_check.py`, 16 тестов
      (`test_a189_*`); живьём — тот же шаг «A186 A189» в собранном образе: api и migrate «годен,
      режим prod», отказ импорта `DB_POOL_SIZE` у migrate — код 1 без `Traceback` и значения
- [x] A192 — `test_a192_prod_api_names_the_missing_bot_pair`,
      `test_a192_placeholder_token_is_refused`,
      `test_a192_chat_that_is_not_an_integer_is_refused_without_its_value`,
      `test_a192_group_and_private_chat_numbers_start`,
      `test_a192_worker_and_migrate_start_without_the_bot_pair`,
      `test_a192_token_is_a_secret_by_type`,
      `test_a192_bot_token_is_masked_without_a_manual_mark`,
      `test_a192_bot_token_in_an_alert_line_becomes_the_mask`,
      `test_a192_bot_pair_goes_to_the_stack_environment`; оракул — пара бота в окружении api
      (`stack_env`), A64 — семь имён
- [x] A23 — сканер правила LOGIC-10: слова и пояса строки A23 спеки и «пояс машины» (рабочий
      инструмент сессии вне репозитория), по `3500a65..e793f93` и документам T5 в индексе:
      добавленных строк 3938, файлов A/M 54 — совпадений 8, все — текст самого правила A23 в
      подписанных копиях спеки (`spec.md:108`, `ch1-06-spec.md:199`); в коде и в своих
      документах — 0; коммитов 37, даты только +0000, в сообщениях — 0
