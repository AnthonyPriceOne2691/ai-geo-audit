# Eval smoke — Ч1.5б «Веб и защиты стека»

Derived from spec acceptance. Run during verify. Живой оракул стека — на машине исполнителя на
`2fdfa3a`, 2026-11-23T14:18:36–14:21:52Z: шагов 25, красных 0, своего не осталось; строки — в
отчёте, «Исполнение рисковых путей». CI — прогон 45362569783 на `22d1aa9`, зелёный целиком: шаги
оракула — в джобе `stack`, скан образа web — в `image-scan`. Пробы проверяющего — 2026-11-24 на
копии `review-05b2` (`36c1a43`), отчёт, «Пробы проверяющего». Набор тестов на `20bde77` — 1054
из 1054 под `-W error`. Живые порчи — отчёт, «Behavior oracles»; порчи проверяющего на функции
суда шагов — 19: убито 18, W7 равнозначна (проба).

- [x] A77, первая строка — шаги «A77 сборка образа web с канарейками» и «A77 стадия сборки,
      контекст и файлы образа web»: канарейки с меткой прогона — `frontend/.env`, файл в
      `frontend/node_modules/`, файлы в `backend/` и в своём каталоге верхнего уровня; `npm ci`,
      `npm run build`, `npm run check:dist` прошли; в контексте — только открытые пути; корень
      сайта — ровно 5 файлов `dist/`, в `conf.d` — `web.conf`. Тесты
      `test_a77_web_context_is_frontend_without_closed_paths_and_deploy_web`,
      `test_a77_web_image_has_exactly_dist_and_our_conf`,
      `test_a77_empty_dist_is_red_even_with_an_empty_site_root`
- [x] A77, вторая строка — живая порча агента среза 1, 2026-11-23T11:08:52–11:08:57Z: карты
      исходников в `frontend/vite.config.ts` и снятый `npm run check:dist` в
      `deploy/web/Dockerfile` — «A77 стадия сборки, контекст и файлы образа web — КРАСНОЕ: в
      стадии сборки нет npm run check:dist; в образе лишнее:
      ['usr/share/nginx/html/assets/index-Bvxqv_9o.js.map']», шагов 17, красных 1. Тест — случай
      `.map` в `test_a77_web_image_has_exactly_dist_and_our_conf`
- [x] A77, третья строка — `test_a60_a77_dockerfile_pins_bases_and_installs_by_lock`: `FROM` —
      `node` 24-alpine и `nginx` 1.30-alpine с дайджестом, установка — `npm ci`;
      `test_a60_a77_spoiled_dockerfile_is_red_with_the_line_number`: порчи web «тег nginx без
      дайджеста» и «`npm install` вместо `npm ci`» — красное с номером строки
- [x] A78 — шаг «A78 страница и кэш web»: `/`, `/x/y` — 200, `index.html` из `dist/`,
      `no-cache`; файл `/assets/…woff2` — `public, max-age=31536000, immutable`. Живые порчи
      «снят `try_files`» и «два `Cache-Control`» — красные; тест
      `test_a78_a80_web_step_on_a_spoiled_web_is_red_with_the_reason`. Проба проверяющего
      (`curl -i`): у файлов сборки — год и `immutable`
- [x] A79, первая строка — шаг «A79 заголовки защиты web»: у страницы, файла сборки,
      `/api/health` 200 и `/api/nope` 403 — по одному каждому из пяти заголовков, `Server` без
      версии, у `/api/` — `no-store` и `X-Request-ID` сервиса; 502 — в шаге «A84 A83 A79 отказы
      /api/ и журнал web»: образец «прокси» с `no-store`. Живые порчи «location без `include`»,
      «`server_tokens on`», «снят `always`» (на 502) и «снят `proxy_hide_header`» — красные.
      Тесты — таблица порч шага заголовков и
      `test_a84_refusal_is_the_proxy_sample_with_no_store`. Проба проверяющего (`curl -i` пяти
      путей): заголовки защиты по одному, у `/api/` — один `nosniff` и `no-store` сервиса
- [x] A79, вторая строка — разбор `nginx.conf`:
      `test_a79_snippet_is_included_at_server_level_and_in_each_location_with_add_header` и
      `test_a79_every_snippet_header_is_always_and_server_tokens_is_off`; порча «location со
      своим `add_header` без `include`» — красный тест
      `test_a79_a80_a83_spoiled_web_conf_is_red_with_the_name` и красный живой оракул
- [x] A80 — шаг «A80 CSP страницы web»: `/`, `/x/y` — ровно десять директив спеки, COOP —
      `same-origin`. Тест `test_a80_page_csp_has_exactly_the_ten_spec_directives_and_coop`
      сверяет каждую директиву литералами спеки (L36); три живые порчи CSP — красные. Проба
      проверяющего — Chrome 158 без окна: `/` и `/x/y` в светлой и тёмной теме — нарушений CSP
      0; контрольная страница с тремя нарушениями — все три в консоли
- [x] A81 — шаг «A81 пересоздание api за web»: 200; адрес api 172.16.4.226 → 172.16.4.227 —
      прежний занят разовым контейнером (L108); снова 200 через 1,6 с после healthy;
      `State.StartedAt` web прежний. Живая порча «`proxy_pass` без переменной» — красная.
      «`resolver` без `valid`» живьём зелёная — её держит разбор `nginx.conf` без Docker
      (`850f33e`, L107)
- [x] A82, первая строка — шаг «A82 процессы, порт и корень web»: uid 101; слушает 8080 и 40245
      (второй — встроенный DNS докера, проба 2026-11-23); корень — только чтение;
      `127.0.0.1:18178` — 200. Живые порчи «снят `USER`», «порт на всех адресах» и «снят
      `read_only`» — красные. Проба проверяющего — `docker inspect`: порт только у web, на петле
- [x] A82, вторая строка — шаг «A82 web без api 40 с»: web healthy все 40 с, затем api снова
      healthy. Порча «в проверке web стоит `/api/health`» в срезе 3 — web unhealthy через 29,4 с
      после остановки api
- [x] A82, третья строка — `test_a82_web_port_and_front_subnet_come_from_the_stack_name`: у
      двух имён — разные порт и подсеть из своего ряда; занятый кандидат — следующий; пять
      занятых — отказ «заняты все 5 кандидатов», шестой не пробуется;
      `test_a82_candidate_is_taken_by_a_bound_port_or_an_overlapping_docker_net` и
      `test_a82_slot_reads_docker_nets_once_and_skips_a_candidate_in_one`. Живьём «кандидат
      занят» в двух прогонах разом не встретился — пробел отчёта
- [x] A82, четвёртая строка — два живых прогона разом из двух копий на `51f88c4`, второй через
      30 с: оба «шагов 25, красных 0»; порт 18178 и 18177, `front` 172.16.4.224/28 и
      172.16.4.208/28. У проверяющего 2026-11-24 — стеки `…-784254e99e` и `…-75e082cd6c`, оба
      «шагов 25, красных 0», своего не осталось
- [x] A83, первая строка — шаг «A83 адрес клиента api через web и напрямую»: через web
      `client` — 198.51.100.83; напрямую из `front` — 172.16.4.227, присланный не принят. Живые
      порчи «`API_TRUSTED_PROXIES` подсетью `front`» и «`$proxy_add_x_forwarded_for`» —
      красные; тест `test_a83_client_field_is_read_per_request_id`
- [x] A83, вторая строка — шаг «A84 A83 A79 отказы /api/ и журнал web»: метка в параметре, в
      `Authorization` и `X-Access-Token` при живом, замершем и остановленном api и на `/x/y` — в
      журнале web её нет; своих строк — 6, по маске, путь как прислан. Тесты
      `test_a83_access_log_fields_and_error_log_level` и
      `test_a83_journal_has_no_mark_and_a_line_per_answer_besides_health_checks`; живые порчи
      «формат журнала образа» и «журнал ошибок с `error`» — красные
- [x] A84, первая строка — тот же шаг: 413; 1 048 576 — ответ api 405 с `X-Request-ID`; api на
      паузе — 504 через 30,0 с; остановлен — 502; отказы — образцы «прокси» с `no-store`. Живые
      порчи «сняты `error_page`», «`client_max_body_size 100k`» и «текст отказа только в
      `nginx.conf`» — красные. Граница 1 048 576: 502 — 0 из 400 (decisions). Пробы
      проверяющего: 1 048 577 — 413 JSON `too-large` с `no-store`; ровно 1 048 576 — до api,
      405; api на паузе — 504 через 30 с. Остановленный api в первые 10 с — 504 через 60 с:
      пробел отчёта, решение — Ч1.6
- [x] A84, вторая строка — `test_a79_a83_a84_api_location_and_its_refusals_follow_the_spec`:
      пределы равны — 1 048 576 байт; текст `too-large` один у сервиса и у прокси; порчи пределов
      в обе стороны и текстов — красные, печатают оба числа или оба текста
- [x] A86 — шаг «A86 выход наружу и сети»: api и migrate наружу — `ENETUNREACH`; из web:
      `db:5432` — `gaierror`, `redis:6379` — `gaierror`; обычная сеть только у web. Живые порчи
      «у `data` снят `internal`» и «web в `data`» — красные; на Docker Desktop первая даёт
      `connected`, а не тайм-аут (L109): её держит и проверка флагов сетей. Тесты
      `test_a86_outward_is_enetunreach_in_under_a_second`,
      `test_a86_web_has_no_connection_to_db_and_redis` и
      `test_a86_only_web_has_an_ordinary_network`
- [x] A87, первая строка — шаг «A87 права и корень контейнеров»: db, redis, migrate, api, web —
      `CapDrop [ALL]`, `no-new-privileges:true`, корень — только чтение, у db — `tmpfs`. Живые
      порчи «сняты `cap_drop`, `no-new-privileges`, `read_only`» — красные. Тест
      `test_a87_container_privileges_are_judged_by_host_config`: без флага `ReadonlyRootfs` и с
      лишним путём `tmpfs` у db — красное (`20bde77`). Проба проверяющего — `docker inspect`
      пяти: у db `Tmpfs` — `/tmp` и `/var/run/postgresql`
- [x] A87, вторая строка — `test_a87_each_hardening_rule_has_its_probe`: проба на каждое
      условие семи правил — красное с кодом правила и именем сервиса; без правила его проба
      проходит, и тест красный; `test_a87_neighbour_cases_are_not_violations` — соседние случаи
      не нарушения; `test_a87_root_is_read_only_everywhere_and_the_exception_list_is_empty`
- [x] A87, третья строка — шаг «A73 A87 правила на настоящем docker-compose.yml»: «сервисов 5,
      правил 23, нарушений 0»; `config --services` — 5. Тесты
      `test_a73_a87_rule_codes_are_the_sixteen_of_a71_a72_and_seven_of_a87_in_order` и
      `test_a73_a87_summary_names_services_and_rules`
- [x] A23 — сканер правила LOGIC-10 (рабочий инструмент сессии, вне репозитория) по новым и
      изменённым файлам поставки против main `fe0f367`: на `329aa47`, 2026-11-23T14:33:16Z, —
      одно совпадение, строка A23 спеки (текст правила). Повтор — на коммите сдачи
