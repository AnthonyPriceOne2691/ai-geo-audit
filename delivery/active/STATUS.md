# Active delivery status
- **slug:** ch1-06a3-deploy-check
- **stack:** delivery@2.30, cqg@3.30, okf@absent
- **stack-selftest:** external (тексты канона в корне рабочей копии, вне git; ревизия 212443d)
- **class:** L
  <!-- 6а-3 «Предполётная проверка и памятка» — срезы 6–7 плана 6а и реляционный оракул ядра
       (`split.md`, п. 6). Подъём боевого наложения в CI — в 6в, как подписано (решение ведущей
       2026-11-27). Скрипт выката запускают от root, `deploy.py check` судит общую машину и файлы
       соседа — полная приёмка. Спека и план — общие с 6а-1 и 6а-2 (архивы
       `2026-11-23-ch1-06a1-deploy-core` и `2026-11-26-ch1-06a2-deploy-prod`), разрез —
       `split.md`, его последний раздел. -->
- **kind:** feature
- **repro_test:** n/a reason=не bugfix
- **diagnosis:** n/a reason=не bugfix
- **phase:** specify
- **builder:** agent:hands-session
- **verifier:** agent:reviewer3-session
  <!-- Третья проверяющая — решение ведущей 2026-11-27: полная приёмка, чтение черновиков —
       2026-11-27, до показа владельцу. -->
- **human_ok_spec:** deferred (reason=черновик 2026-11-26 на main `bd59f31`; подпись примеров 2026-11-20T08:32Z действует, новое слово — на «было — станет» пп. 1–5 `split.md`)
- **human_ok_plan:** deferred (reason=то же слово: пп. 2–5 `split.md`)
- **shape-oracles:** cqg-deployed
- **behavior-oracles:** tests-present — pytest без Docker: `deploy.py check` с подставным исполнителем на временном корне — права и заглушки файла сервера, образы (A181, третья строка), имена против меток compose, порт живой и из файлов соседа, подсеть, ресурсы, пары «пароль — адрес» (A185; A189, вторая строка); реляционный оракул `@given` ядра — `Files.own`, `site_name`, `health_urls`; A183 и A186 растут на `deploy.py check`; сканер A23
- **artifact_oracle:** scripts/stack_oracle.py — шаг до уборки оракула: живой `deploy.py check` от root (`sudo -n`, только в CI) — имена, порт и подсеть живого стека оракула, вымышленный каталог compose «соседа», затем чистые значения; строку ресурсов не судит
- **ci-oracles:** deployed
- **worktree:** .claude/worktrees/ch1-05c
  <!-- Копия Ч1.5в под старым именем каталога, в ней шли «Учётки-1», «Учётки-2» и 6а-2; решение
       ведущей 2026-11-26 — 6а-3 в этой же копии; ветка — от main после слова владельца. -->
- **parallel_sessions:** manual-deploy
- **hooks:** not-deployed
- **blockers:** none
- **waivers:** none
- **runtime_paths:** deploy/**
  <!-- Скрипт выката виден только исполнением: живые прогоны в джобе `stack` с выдуманными
       значениями от root на путях одноразовой машины — `deploy.py check` шагом оракула до уборки;
       вывод — в verify-report, блок «Исполнение рисковых путей» (план 6а). -->
- **irreversible_surfaces:** публикация репозитория (публичный GitHub — создание и push), выкат на общий сервер, любая трата на платных сервисах, выпуск отчёта клиенту
  <!-- Подпись объявления — окном `scripts/sign_irreversible.sh` касанием владельца по этой
       поставке, своё окно 6а-3 (`split.md`: «6а-3 — своё окно и своя сдача»), после коммита
       подписи ведущей и отдельного «да» владельца; файлы подписи вносит ведущая. -->
- **model_surface:** n/a reason=модель продукт не вызывает; поверхность объявляется с первым вызовом модели — волна В3 (§14)
- **rule_enforcers:** n/a reason=model_surface не объявлена
- **canon_drift_waiver:** no
- **baseline_growth_waiver:** no
  <!-- «1100 / 1150» — до слова владельца (`split.md`, «6а-3: было — станет», п. 4): строка
       `[exemption]` гейта длины для `deploy/deploy.py`, сейчас снимок 739, лимит 789. -->
- **observability:** 1
- **observe_signal:** <заполняется на handoff>
- **observe_until:** <заполняется на handoff>
- **circuit_breakers:** max_loc_diff=2500 max_files_touched=50
