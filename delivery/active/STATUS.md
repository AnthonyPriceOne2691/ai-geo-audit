# Active delivery status
- **slug:** storage-creds-1
- **stack:** delivery@2.30, cqg@3.30, okf@absent
- **stack-selftest:** external (тексты канона в корне рабочей копии, вне git; ревизия 212443d)
- **class:** L
  <!-- Предложение исполнителя: права базы и пароли двух ролей; новый скрипт
       `deploy/db/roles.sql`, который суперпользователь исполняет в контейнере базы — сам на
       пустом томе и командой на каждом выкате; проверка старта по роли процесса. База — в списке
       владельца на полную приёмку (слово 2026-11-19). План общий для «Учёток-1» и «Учёток-2»;
       19.11 владелец подписал только примеры с блоком замены чисел, поэтому `human_ok_plan` —
       новым словом. Разрез — `split.md`. -->
- **kind:** feature
- **repro_test:** n/a reason=не bugfix
- **diagnosis:** n/a reason=не bugfix
- **phase:** tasks
- **builder:** agent:hands-session
- **verifier:** agent:reviewer3-session
- **human_ok_spec:** pending
  <!-- Подпись и комментарий вносит ведущая своим коммитом. -->
- **human_ok_plan:** pending
- **shape-oracles:** cqg-deployed
- **behavior-oracles:** tests-present — pytest без Docker: проверка роли процесса (A174); оракул стека на машине и в CI — роли и права базы (A85), повтор скрипта ролей, смена пароля, стенд до поставки и отзыв лишнего (A170), упавший скрипт без паролей в выводе и журнале (A172, вторая строка)
- **artifact_oracle:** scripts/stack_oracle.py — живые шаги ролей и прав базы, повтора и смены пароля скрипта ролей, упавшего скрипта
- **ci-oracles:** deployed
- **worktree:** .claude/worktrees/ch1-05c
  <!-- Копия Ч1.5в под старым именем каталога: изолированная сессия новую не заводит; решение
       ведущей 2026-11-24 — вести «Учётки» в ней, ветка `storage-creds` от `8adca75`. -->
- **parallel_sessions:** manual-deploy
- **hooks:** not-deployed
- **blockers:** none
- **waivers:** none
- **runtime_paths:** deploy/db/
- **irreversible_surfaces:** публикация репозитория (публичный GitHub — создание и push), выкат на общий сервер, любая трата на платных сервисах, выпуск отчёта клиенту
  <!-- Подпись объявления — окном `scripts/sign_irreversible.sh` касанием владельца по этой
       поставке (слово владельца 2026-11-24T08:47:28Z), после коммита подписи ведущей и
       отдельного «да» владельца; файлы подписи вносит ведущая. -->
- **model_surface:** n/a reason=модель продукт не вызывает; поверхность объявляется с первым вызовом модели — волна В3 (§14)
- **rule_enforcers:** n/a reason=model_surface не объявлена
- **canon_drift_waiver:** no
- **baseline_growth_waiver:** no
- **observability:** 1
- **observe_signal:** <заполняется на handoff>
- **observe_until:** <заполняется на handoff>
- **circuit_breakers:** max_loc_diff=2500 max_files_touched=50
