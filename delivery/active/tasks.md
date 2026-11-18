# Tasks — contour-b0 (шаг 0 и волна В0: ось ① Delivery)

Уроки архива: `archive/INDEX.md` пуст — поставка первая, пересечений по путям нет.

## Slice 1 — шаг 0: репозиторий и вариант D
- [x] T1: `git init -b main` в корне проекта; автор коммитов задан только в этом репозитории.
- [x] T2: строки `.git/info/exclude` до первого `git add`: восемь имён текстов контура
      (AGENT_STACK §7.1a, дословно) и локальные папки проекта. Проверено:
      `git add -A --dry-run` — 0 файлов, `git check-ignore -v --no-index` покрывает каждое имя.
- [x] T3: тексты канонов одной ревизии (коммит канона 5d82ab1) — в корень, после игнора.
      `python3 extract_payload.py --check-local .` — 0 и 0; контроль: свежий клон без
      строк игнора даёт «без игнора 7» и код выхода 1.
- [x] T4: коммит шага 0 на main: README и `.gitignore`, без кода.

## Slice 2 — ось ① Delivery по §7.2
- [x] T5: payload извлечён во временную папку; пути канона delivery взяты по манифесту
      (`--manifest --by-canon`), список на диске сверен с ним сравнением.
- [x] T6: дерево §2.3 для класса S: CONSTITUTION, README, STACK-ACCEPTANCE, `active/.gitkeep`,
      STATUS, tasks, decisions, verify-report, `archive/INDEX.md`, `evals/smoke/README.md`.
- [x] T7: конституция под продукт; блок `agent-permissions` = `.claude/settings.json`
      (записан по слову владельца).
- [x] T8: блок-крючок A.5 в `AGENTS.md`.
- [x] T9: `scripts/delivery_check.py` со всеми частями и `scripts/delivery_metrics.py`.
- [x] T10: подпись необратимого парой скриптов (§7.2 п.5а): `check_irreversible_signature.sh`,
      `sign_irreversible.sh`, публичный ключ владельца в `.github/allowed_signers`.
- [x] T11: подпись объявления `irreversible_surfaces:` — поставил владелец своим ключом
      (Secretive) 2026-11-18 14:19; `check_irreversible_signature.sh` — сходится.
- [x] T12: STATUS первой поставки: `kind: bootstrap`, версии, непришедшие оси с причиной
      и волной, необратимое объявлено, поля про исполнение.
- [x] T13: гейт поставки в чистом клоне (`--diff-base origin/main`); числа ошибок и
      предупреждений — в verify-report.
- [x] T14: приёмка `STACK-ACCEPTANCE.md`, включая «где процедура подвела».
- [ ] T15: вердикт проверяющего (ставит проверяющая сессия), затем перемотка main.
