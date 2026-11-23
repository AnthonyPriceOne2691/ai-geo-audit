# Verify report — правка 6а-1: тесты выката в раскладке mutmut

**Date:** 2026-11-23
**Verifier:** agent:lead-session
**asserts_reviewed_by:** human:owner at=2026-11-23T10:15Z
<!-- Слово владельца «да на всё, подписываю оракул» 2026-11-23T10:15Z — на список ведущей, п. 1:
     «Правка тестов ядра выката — опубликовать в основную ветку по готовности». Утверждений
     правки два, оба в тесте-повторе: подпроцесс кода 0 и путь скрипта — корень копии. Вносит
     проверяющий своим коммитом. -->
**CI run:** после выкладки проверяющим — ветка-проба с меткой в одном файле `backend/src`: шаг статистики mutmut
**Commit:** `2e233d6` — вершина правки; прогон в чистом клоне ниже

Класс S, `kind: bugfix`: мини-спека — `tasks.md`, журнал гипотез — `diagnosis.md`.

## Повтор и правка

- Красный — `ed8cdc7`: тест-повтор на коде `4b46e66` падает `FileNotFoundError` на
  `<копия>/backend/deploy/deploy.py` — как шаг статистики mutmut в прогоне 45150681177.
- Правка — `fa8c617`: `deploy_fakes.py` берёт корень копии у `envguard` (метки
  `docker-compose.dev.yml` и `backend/`), а не по глубине. Код `deploy/` и тесты выката не
  тронуты.
- Запись «Разбор» в `delivery/STACK-ACCEPTANCE.md` — `2e233d6`.

## Прогон в чистом клоне

Один прогон на `2e233d6`, с 2026-11-23T10:04:01Z по 10:05:51Z. Клон свежий (`--no-local`), база
диффа — `origin/main` клона, `4b46e66`. Последняя команда кладёт копию `backend/tests` в
`backend/mutants/tests` и гоняет набор оттуда, как шаг статистики mutmut. Вывод дословный,
длинные строки обрезаны знаком «…»:

```
$ git clone -q --no-local -b ch1-06a1-fix-mutants-root <репозиторий> <клон> && cd <клон>
$ git checkout -q 2e233d6e74d72030202aba70d56211dc92e94260
$ git log --format="%h %p %s" -5 && git rev-parse --short origin/main
2e233d6 fa8c617 docs(delivery): STACK-ACCEPTANCE — разбор: тесты, ломающиеся под раскладкой mutmut,…
fa8c617 ed8cdc7 fix(deploy): правка 6а-1 — корень копии в помощнике тестов выката по меткам envguard
ed8cdc7 0886ab3 test(deploy): правка 6а-1 — красный: помощник тестов выката не находит скрипт из ра…
0886ab3 4b46e66 docs(delivery): правка 6а-1 — тесты выката в раскладке mutmut: статус, мини-спека, …
4b46e66 77b143b feat(deploy): Ч1.6а-1 — ядро скрипта выката (#11)
4b46e66
$ python3 scripts/delivery_check.py --diff-base origin/main; echo "exit=$?"
breakers: files=2 net_loc=41 (+42/-1), excluded=4 (delivery/|knowledge/|scripts/lint/|scripts/merge…
наблюдение §13.1: поставок в архиве 14, окон со сроком 14, истекших без observed.md 0
сквозные проверки §6.1: сценариев в таблице 0 (delivery/evals/smoke/README.md)
WARNING: delivery/evals/smoke/README.md: таблица сценариев пуста, а код уже поставлен — в архиве ес…
delivery_check: 0 error(s), 1 warning(s)
exit=0
$ (cd backend && uv sync --frozen -q); echo "uv sync exit=$?"
uv sync exit=0
$ backend/.venv/bin/pre-commit run --all-files > hooks.txt; echo "exit=$?"; \
    grep -c 'Passed$' hooks.txt; grep -c 'Skipped$' hooks.txt; grep 'Failed$' hooks.txt
exit=0
27
1
$ backend/.venv/bin/pre-commit run --all-files --hook-stage pre-push; echo "exit=$?"
check for added large files.......................................................Passed
dead code — объявлено и никем не зовётся (ратчет, pre-push).......................Passed
delivery phase gate + breakers (pre-push).........................................Passed
baseline only melts (pre-push)....................................................Passed
новая зависимость объявлена в STATUS (pre-push)...................................Passed
exit=0
$ cd backend && uv run --frozen pytest -q -p no:cacheprovider -W error | tail -1; \
    echo "pytest exit=${PIPESTATUS[0]}"
собрано 763, прошло 763, пропущено 0
pytest exit=0
$ cd backend && cp -R tests mutants/tests && cd mutants \
    && ../.venv/bin/python -m pytest -q -p no:cacheprovider -W error tests | tail -1; \
    echo "pytest exit=${PIPESTATUS[0]}"
собрано 763, прошло 763, пропущено 0
pytest exit=0
```

- Счёт — 41 строка в 2 файлах при пределах 2500 и 50. Предупреждение о пустой таблице сквозных
  проверок — контура (`delivery@2.23`), не этой поставки.
- Тестовая база клона снята после прогона, клон удалён.

## Ревью рисковых мест

- **Новый модуль** — `backend/tests/test_mutants_layout.py`. Тест кладёт копию трёх помощников и
  скрипта выката во временный каталог и импортирует `deploy_fakes` в подпроцессе `-I` с пределом
  60 с. Рабочую копию и её `backend/mutants/` он не трогает. Путь к образцу скрипта берёт через
  `envguard.ROOT`, поэтому зелёный и сам под раскладкой mutmut (прогон выше).

## Shape oracles
- [x] PASS — хуки стадии коммита на всех файлах — 27 зелёных и 1 пропуск; стадия pre-push — 5 из 5.

## Behavior oracles
- [x] PASS — тест-повтор: красный на `ed8cdc7`, зелёный на `fa8c617`.
- [x] PASS — полный набор в чистом клоне — 763 из 763 под `-W error`, пропусков нет; тот же набор
      из раскладки mutmut — 763 из 763.

## Product oracles
- [x] n/a — поведение сервиса и скрипта выката не меняется: правка помощника тестов.

## Spec coverage gaps
- Довод «под mutmut тесты выката собираются» пока держит проба раскладки, а не сам mutmut.
  Доказательство в CI — ветка-проба проверяющего с меткой в одном файле `backend/src`: шаг
  статистики mutmut обязан пройти.

## Verdict
- [x] READY FOR HANDOFF
- [ ] NEED CONVERGE (new tasks)
- [ ] BLOCKED

Вердикт проверяющего на `ee8fa60`, 2026-11-23: к сдаче.

Что проверила сама:
- правку и тест прочла: корень — `envguard.ROOT` (метки `docker-compose.dev.yml` и `backend/`),
  одна точка поиска корня на весь набор; тест-повтор работает во временной папке и настоящий
  `backend/mutants/` не трогает;
- тест-повтор в своей копии: на `ee8fa60` — «1 passed»; с `deploy_fakes.py` из `4b46e66` —
  `FileNotFoundError … copy/backend/deploy/deploy.py`, как в прогоне 45150681177;
- CI 45153667357 на `ee8fa60` — gates, delivery, tests зелёные;
- проба в CI 45153677939 (`1b36c26`, метка в `api/health.py`): шаг статистики mutmut прошёл —
  «mutation: судил файлов 1 из 1, мутантов 37», «убито 28 из 37 (75%)», «mutation: OK»;
- скан правки к `4b46e66` чистый: одно совпадение «путь машины» — путь раннера GitHub в
  цитате журнала CI;
- объявление необратимого — те же четыре поверхности и та же подпись, что у 6а-1.

## Harness metrics (this shipment)

<!-- generated by scripts/delivery_metrics.py --base main; ручные поля — на handoff -->

| Metric | Value |
|---|---|
| files_touched / loc_diff | 2 code (+7 outside breakers §3.4) / +42/-1 (net +41) |
| commits | 9 — с вердиктом и подписью проверяющего и коммитом сдачи; прогон на нём — следующим |
| time_to_accepted_spec | n/a (no spec.md in history — class S?) |
| rework_after_done | 0 — после вердикта «к сдаче» правок нет |
| harness_hardened | yes — backend/tests/test_mutants_layout.py (новый оракул) |
| implement_retries | 1 — коммит пробы гейта порч (`1b36c26`) не пустил хук ruff format: перед комментарием верхнего уровня нужны две пустые строки |
| verify_fails_before_green | 1 — проверяющий до выкладки вернул объявление необратимого: «none» → четыре поверхности и подпись (`ee8fa60`); CI 45153667357 на `ee8fa60` и проба 45153677939 — зелёные с первого раза; отказов pre-push нет |
| est_token_or_cost | n/a — работа по подписке, расход сессии не измерялся |

**Убрано (CONSTITUTION, п. 10):** клон прогона и его база `audit_test_b148e57e9e`;
раскладка `backend/mutants/` проб в копии; разовые сценарии и журналы прогонов. После
прогона на коммите сдачи — его клон и тестовая база. После публикации — копия
`.claude/worktrees/ch1-06a1-fix`, локальные ветки `ch1-06a1-fix-mutants-root` и
`probe-mutants-root` и база копии `audit_test_075254670c`.
