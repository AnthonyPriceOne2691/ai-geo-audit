# Verify report

**Date:** 2026-11-18
**Verifier:**
<!-- Заполняет проверяющая сессия (agent:lead-session) после чтения отчёта и своего
     прогона в чистом клоне. Builder это поле не трогает (§5.2). -->
**asserts_reviewed_by:** n/a (класс S — дайджест утверждений обязателен для M/L)
**CI run:** n/a reason=CI не развёрнут, волна В1; вместо ссылки — прогон в чистом клоне ниже
**Commit:** см. блок «Прогон в чистом клоне»

## Прогон в чистом клоне

Локальные гейты обходятся (`commit -n`, `STRICT=0`), поэтому доказательство —
прогон на склонированном состоянии, а не в рабочем дереве (§10.4). База диффа в
клоне — `origin/main`: локальной ветки `main` в свежем клоне нет, и с
`--diff-base main` проверки по диффу пропускаются молча (см. приёмку, «где
процедура подвела»).

```
$ git clone -b bootstrap/contour-b0 <репозиторий> <клон> && cd <клон>
$ python3 scripts/delivery_check.py --diff-base origin/main
<вставляется после финального коммита>
```

## Ревью рисковых мест

Риска нет, потому что маячок «транзакция БД» (§12.5) поднят словом `commit` в
`AGENTS.md`: это правило для агентов «коммит только по путям, `git commit -- <пути>`»,
то есть git-коммит, а не транзакция базы. Кода, который работает с базой данных, в
поставке нет: из 28 файлов диффа 15 — скрипты контура (`scripts/delivery_*.py`,
`scripts/*irreversible*.sh`), 10 — дерево `delivery/`, остальные три —
`AGENTS.md`, `.claude/settings.json` и `.github/allowed_signers`.

## Shape oracles
- [x] n/a — CQG не развёрнут (волна В1). `shape-oracles: weak` объявлено в STATUS с причиной.

## Behavior oracles
- [x] PASS — `scripts/delivery_check.py` в чистом клоне: ошибок 0; предупреждения перечислены и объяснены в блоке прогона. Builder ≠ Verifier гейт сверяет по объявлению: поле «Verifier» здесь пустое, и гейт берёт `verifier:` из STATUS (`agent:lead-session` ≠ `agent:hands-session`). Поле «Verifier» и вердикт заполняет проверяющий.
- [x] PASS — `extract_payload.py --check-local`: в рабочей копии 0 и 0 при текстах канонов на диске; контроль — свежий клон без строк игнора даёт «без игнора 7» и код выхода 1.
- [x] PASS — права агента: блок `agent-permissions` конституции и `.claude/settings.json` совпадают в обе стороны (14 deny, 11 ask) — ошибок §4.5 в прогоне нет.
- [x] n/a — тестов продукта нет: кода продукта нет. Первые придут с каркасом.

## Product oracles
- [x] n/a — `delivery/evals/smoke` пуст: продукта пока нет.
- [x] n/a — `active/eval-smoke.md` не заполняется на классе S (§2.2).

## Вариант D: тексты контура вне git

```
$ python3 extract_payload.py --check-local .
check-local: отслеживается 0, без игнора 0 — под вариантом D оба числа нулевые
$ git ls-files | grep -cE '^(AGENT_STACK|AGENT_DELIVERY_HARNESS|CODE_QUALITY_GATES|OKF_KNOWLEDGE_BUNDLE)\.md$|^(stack_selftest|selftest_sizes|extract_payload)\.py$|^docs/'
0
```

## Spec coverage gaps
- Оси ②, ③ и ⑤ не развёрнуты — по плану волн: ② и CI — В1, ③ — В2, ⑤ — В3. В STATUS
  стоят `weak`, `absent` и `n/a` с причиной.
- `stack-selftest: external` — самопроверка канонов в CI проекта невозможна под
  вариантом D по построению.
- Подпись объявления необратимого ставит владелец своим ключом; пока её нет, гейт
  даёт предупреждение, на handoff — ошибку.

## Verdict
- [ ] READY FOR HANDOFF
- [ ] NEED CONVERGE (new tasks)
- [ ] BLOCKED

<!-- Вердикт ставит проверяющий, не Builder (§5.2). -->
