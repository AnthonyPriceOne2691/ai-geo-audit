# Tasks — smoke-run (сквозные проверки smoke, §6.1, класс M)

Уроки архива по путям поставки — в `plan.md`, «Уроки до кода»: L73, L28, L19, L55, L30, L83, L85,
L97, L89, L60, L91, L69, L64, L88, L94, L95, L80, L61, L50, L47, L67, L101. Предел — 2500 строк,
порог «сказать до сдачи» — 2300.

## Slice 1 — README, `run.sh` и сценарии
- [ ] T1: тесты красными — подставные сценарии: первый сбой, предел, ноль, окно 15 строк (A373,
      A375); замок (A378); остатки и чужой лидер (A379); сверка пакета параметрами и путь через
      ссылку, нет `backend/.venv`, `uv` без `--no-sync`, `PYTHONPATH` в `alembic_env` (A377); S3 с
      кодом 0 без ревизии (A374); исход `SIGTERM` (A371); S1 при занятом порте (A373); команда S2 —
      шаг «Тесты с покрытием» без `--cov…` (A372); таблица README и помощник (A375).
- [ ] T2: `backend/tests/smoke_run.py`; `backend/tests/live_server.py` — общие с A55 `free_port` и
      `live_env`; `dbtools.alembic_env` — `PYTHONPATH`; `delivery/evals/smoke/run.sh` и README;
      `.gitignore` — `.smoke/`.
- [ ] T3: настоящие S3 и S1 одним тестом на тестовом Postgres (A370, A371); снос базы набора после
      S2 (A372).

## Slice 2 — CI
- [ ] T4: `quality.yml` — шаг smoke в `tests` после тестов, адаптация шага «Smoke evals» джобы
      `delivery`, запись в `adapted.json`; тест контракта (A376).
- [ ] T5: ветка-проба — прогон на GitHub: S3 и S1, `delivery_check` без предупреждения §6.1, доли
      mutmut с меткой в `backend/src` (L95), цена второго прогона S3 и S1; строки eval-smoke.

## Verify
- [ ] T6: живые пробы — два `run.sh` со сдвигом (L85), `kill -9` во время S1, дерево `merge_guard` в
      `mktemp -d`; порчи помощника руками; чистый клон обеих стадий хуков; verify-report.

## Handoff
- [ ] T7: фаза, метрики §9, наблюдение, уроки — в `archive/INDEX.md`; «Для канона» — одолженное
      окружение `merge_guard`, часть «до smoke шаги гейта пакет не исполняли» — «вывод по чтению».
