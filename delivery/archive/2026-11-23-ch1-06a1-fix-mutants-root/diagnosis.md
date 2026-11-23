# Diagnosis journal

Одна строка на гипотезу, вердикт обязателен.

**Симптом:** прогон 45150681177 — ветка-проба «Скорости CI» от main `4b46e66`, порчи в
`storage/engine.py` и `api/health.py`. Шаг статистики mutmut:
`File ".../backend/mutants/tests/deploy_fakes.py", line 39 … FileNotFoundError: …
'/home/runner/work/ai-geo-audit/ai-geo-audit/backend/deploy/deploy.py'`,
`ERROR tests/test_deploy_core.py`, `failed to collect stats. runner returned 1`. Гейт порч
краснеет «не просудил ничего» на любом запросе с правкой в `backend/src`.

**Repro:** `backend/tests/test_mutants_layout.py::test_deploy_fakes_load_script_from_mutants_layout`.
Вручную, 2026-11-23T09:58Z, копия на `4b46e66`: `backend/tests` скопирован в
`backend/mutants/tests`, оттуда `pytest tests/test_deploy_core.py` — `FileNotFoundError: …
/backend/deploy/deploy.py`, собрано 0.

- H1: `deploy_fakes.py` считает корень копии по глубине (`Path(__file__).resolve().parents[2]`),
  а в `backend/mutants/tests/` файл лежит на уровень глубже → проверил: проба выше, путь в
  ошибке — `<копия>/backend/deploy/deploy.py` → ПОДТВЕРЖДЕНА
- H2: так же по глубине корень считают и другие помощники тестов → проверил:
  `git grep '__file__\|parents\[' main -- backend/tests/`. По глубине до корня считает только
  `deploy_fakes.py:23`. `envguard.repo_root()` ищет по меткам, `dbtools.py:248` и
  `test_run_summary.py:14` берут свой каталог, `test_api_live.py:28` — путь пакета →
  ОПРОВЕРГНУТА
- H3: `test_deploy_marks.py` ломается сам по себе → проверил: `ROOT` у `:99`
  (`git -C ROOT check-ignore`) импортирован из `deploy_fakes` → ОПРОВЕРГНУТА — следствие H1

**Корневая причина:** помощник тестов выката (6а-1) считает корень копии по глубине пути своего
файла. В проекте уже есть поиск корня по меткам — `envguard.repo_root()`: метки
`docker-compose.dev.yml` и `backend/`, докстрока прямо говорит «под mutmut тесты лежат в
`backend/mutants/tests`». Им пользуются `dbtools`, `test_url_passwords` и другие тесты; тесты 6а-1
его не взяли.

**Почему прошло мимо гейтов:** гейт порч зовёт mutmut, только когда запрос меняет файлы
`backend/src`. Запрос 6а-1 (№11) правил `deploy/` и тесты, гейт ответил «изменённых prod-файлов
нет» и mutmut не звал. Локальные прогоны, приёмка и чистые клоны гоняют набор из
`backend/tests/`, раскладку `mutants/` не собирал никто. Кандидат в оракул — запись «Разбор» в
`delivery/STACK-ACCEPTANCE.md`.
