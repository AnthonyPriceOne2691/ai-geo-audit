# Инструкции агентам

Сервис аудита сайта (см. `README.md`). Работа идёт поставками по канон-контуру.
Тексты канонов лежат в корне рабочей копии локально и в git не входят (вариант D):
в свежем клоне их нет.

## Правила этого репозитория

- Перед кодом — `delivery/active/STATUS.md` и `delivery/CONSTITUTION.md`.
- `git add -f` не использовать: тексты канонов и рабочие документы стоят в
  `.git/info/exclude`, и обход игнора увозит их в публичную историю.
- Коммит — только по путям (`git commit -- <пути>`): в рабочем дереве работают
  две сессии.
- Материалы заказчика, настоящие домены клиентов, имена людей, ключи и `.env` в
  репозиторий не кладутся.
- Убирай за собой то, что для работы уже не нужно: тестовые базы снятых рабочих
  копий, ветки и рабочие копии слитых поставок, временные скрипты и файлы, мёртвый
  код. Чужое не удаляй — скажи тому, чьё оно (`delivery/CONSTITUTION.md`, п. 10).

## Canon stack / Agent Delivery Harness

- **Start here:** `AGENT_STACK.md` (order: Delivery → CQG → OKF).
- Process canon: `AGENT_DELIVERY_HARNESS.md`.
- Active delivery: `delivery/active/STATUS.md` — read before coding.
- **Order:** follow delivery phases. Do not oneshot large work.
- **Done:** only when verify oracles pass; never declare done on red.
- Prefer git worktree for class M/L (Delivery §5.1).
- Smoke/evals: `delivery/evals/smoke/` + `active/eval-smoke.md` (Delivery §6).
- Metrics on handoff: Delivery §9 / A.10.
- Hooks if deployed: Delivery §10.
- Skills/prompts: `skills/README.md` (Delivery §11); no inline prompts (CQG).
- Do **not** duplicate code-quality rules here — use `CODE_QUALITY_GATES.md` if present.
- Do **not** invent domain canon — use `knowledge/` / OKF if present.
- Deploy order for missing layers: Delivery → CQG → OKF (see `AGENT_STACK.md`).
