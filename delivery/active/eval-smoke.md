# Eval smoke — Ч2.1 «Справочник: таблицы, файл и проверка»

Derived from spec acceptance. Run during verify. Числа прогонов — `verify-report.md`; здесь — чем
держится каждый пример. Тесты — на Postgres стенда разработки (база копии по хешу пути), в CI —
на службе джобы `tests`.

- [ ] A300 — ревизия, сверки моделей и перечислений (`test_reference_models.py`; ветвь A16
      сервиса — `test_models.py`)
- [ ] A301 — ограничения по имени, строка таблицы теста на случай (`test_reference_models.py`)
- [ ] A302 — триггер неизменности на двух редакциях (`test_reference_models.py`)
- [ ] A303 — манифест пакета двумя ветками (`test_reference_file.py`)
- [ ] A304 — `check`: пусто, «было — станет», отменяемые правки, «изменений нет»
      (`test_reference_check.py`)
- [ ] A305, A306, A307 — отказы файла, пределы, тексты заказчика вне вывода
      (`test_reference_file.py`)
- [ ] A308 — манифест в `check` (`test_reference_file.py`)
- [ ] A310 — вход команды в бою, подпроцессом `python -m`; пустой справочник
      (`test_reference_check.py`)
- [ ] A312 — пропавшая строка (`test_reference_check.py`)
- [ ] S3 smoke — `delivery/evals/smoke/run.sh`: сверка «таблицы метаданных есть в базе» видит
      три новые таблицы
- [ ] Живой шаг — стенд compose: `docker compose run --rm -T api python -m
      ai_geo_audit.reference check -` с выдуманным файлом на стандартном вводе — вход в образе api
      проходит проверку старта и читает базу под ролью сервиса; вывод — отказ словами или
      «было — станет», без `Traceback`. Ревизия `0003` под ролью миграций — шаг `stack` CI (A65)
- [ ] A23 — скан по новым и изменённым файлам
