# Eval smoke — 6а-3 «Предполётная проверка и памятка»

Derived from spec acceptance. Run during verify. Строки 6а-3 по `split.md`: A181 (третья), A185
(шесть строк), A189 (вторая), A23; A183, A184 и A186 — «держится с 6а-1», их тесты растут на
`deploy.py check` и реляционный оракул ядра. CI: прогон 45988835354 на `a05e65b` — 11 из 11, тест
с настоящими `find` и `awk` прошёл и на `awk` Ubuntu; 45993253242 на `8d96d53` (T3) — 11 из 11,
в джобе `stack` оракул — шагов 44, красных 0, живой шаг «A185 A183 … — ок: судимых строк 8» в
11:51:08Z; 45996307303 на `400a336` (пара `!override` и T4) — 11 из 11 (итоги — от ведущей).
Оракул на машине исполнителя на `d7585d0` — шагов 44, красных 0, живой шаг вне CI — «пропуск»,
своего после уборки не осталось (2026-11-27T12:14:03Z–12:17:56Z).

- [x] A181, третья строка — образы в файле сервера: `test_a181_check_images_in_the_server_file`
      (до первого выката оба пусты — «образов ещё нет — первый выкат», не отказ; негодное — отказ
      с именем переменной, без значения; дайджест из 65 знаков и хвост после него — против порчи
      `match`)
- [x] A185, первая строка — файл сервера:
      `test_a185_check_refuses_the_server_file_by_name_and_reason`
      (права шире 600, владелец не root, заглушка и пустое у «создаёт скрипт» и «задаёт
      владелец» — отказ с именем и причиной; пустые «пишет выкат» — не отказ)
- [x] A185, вторая строка — имена: `test_a185_check_refuses_our_name_held_by_another_stack`,
      `test_a185_check_own_stack_and_stacks_with_our_prefix_are_no_hindrance`,
      `test_a185_check_foreign_name_containing_ours_is_no_hindrance`; живьём — стек прогона
      оракула с нашей приставкой под другим проектом: «имена — ок» (шаг «A185 A183»)
- [x] A185, третья строка — порт: `ss` и привязки чужих контейнеров, и остановленных
      (`test_a185_check_port_listened_by_another_process_is_taken`,
      `test_a185_check_port_published_by_a_foreign_container_is_taken`,
      `test_a185_check_own_stopped_web_does_not_cover_a_foreign_listener`,
      `test_a185_check_port_against_the_three_server_facts`); файлы соседа — разбор настоящими
      `find` и `awk` (`test_a185_neighbor_files_read_by_real_find_and_awk`,
      `test_a185_neighbor_ports_under_an_override_tag_are_read`,
      `test_a185_check_port_declared_in_a_neighbor_compose_file`); переменная без умолчания,
      список не в строку и якорь — «сверить»
      (`test_a185_check_neighbor_port_by_a_variable_without_default_is_to_check`); живьём —
      «WEB_PORT занят» и «объявлен у соседа» против web прогона и вымышленного соседа
      `/srv/nb-oracle`, «сверить», у чистых значений — «ок»
- [x] A185, четвёртая строка — подсеть:
      `test_a185_check_subnet_overlapping_a_neighbor_docker_network`,
      `…_overlapping_a_neighbor_compose_subnet`, `…_overlapping_a_host_route`,
      `…_inside_the_default_bridge_meets_network_and_route`,
      `test_a185_check_web_address_and_subnet_spelling`,
      `test_a185_check_own_network_and_its_route_are_no_hindrance`; живьём — `FRONT_SUBNET` из сети
      `front` стека прогона — отказ с именем переменной, у чистых значений — «ок»
- [x] A185, пятая строка — ресурсы:
      `test_a185_check_memory_after_our_limits_below_ten_is_refused_with_numbers`,
      `test_a185_check_counts_the_difference_with_our_raised_stack`,
      `test_a185_check_disk_below_twenty_is_refused_with_numbers`,
      `test_a185_check_disk_of_exactly_twenty_passes`,
      `test_a185_check_refuses_an_unread_memory_limit_of_ours`; живой шаг строку ресурсов не
      судит, код выхода идёт за ней
      (`test_a185_live_check_resources_line_is_not_judged_but_the_code_follows_it`)
- [ ] A185, шестая строка — `deploy.py check` на сервере перед первым выкатом: каждая строка —
      «ок» или названное исключение, код 0. Наблюдение ведущей на сервере (§13.1); на машине
      разведки — `test_a185_check_before_the_first_deploy_every_line_ok_or_a_named_exception`
- [x] A189, вторая строка — пары «пароль — адрес»:
      `test_a189_check_refuses_an_address_whose_password_differs`,
      `test_a189_check_refuses_an_address_without_a_password`,
      `test_a189_check_compares_the_password_after_percent_decoding`
- [x] A183 — держится с 6а-1, растёт на `deploy.py check`:
      `test_a183_no_mark_in_output_journal_or_arguments` (метки в значениях, портах и подсетях
      соседа — настоящий `deploy.py check`); живьём — значения владельца окружением через
      `sudo -n --preserve-env`, не аргументами, «значений в выводе 0»
      (`test_a185_a183_live_check_judges_the_collision_then_clean_values`,
      `test_a183_sudo_keeps_values_in_the_environment`)
- [x] A184 и A186 — держатся с 6а-1, реляционный оракул ядра `@given` (`test_deploy_relations.py`:
      `Files.own`, `site_name`, `health_urls`); `deploy.py check` только читает машину и пишет
      свои журнал и замок — `test_a186_check_reads_the_machine_and_writes_only_its_journal_and_lock`
- [x] A23 — сканер правила LOGIC-10 (рабочий инструмент сессии вне репозитория) по
      `e2707ae..ebe03d9`: добавленных строк 3962, файлов A/M 28 — совпадений 8, все — текст
      самого правила A23 в подписанных копиях спеки (`spec.md:108`, `ch1-06-spec.md:199`); в
      коде и в своих документах — 0; коммитов 21, даты только +0000, в сообщениях — 0
