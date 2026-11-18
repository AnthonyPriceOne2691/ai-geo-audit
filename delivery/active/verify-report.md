# Verify report

**Date:** 2026-11-18
**Verifier:** agent:lead-session
<!-- Заполняет проверяющая сессия (agent:lead-session) после чтения отчёта и своего
     прогона в чистом клоне. Builder это поле не трогает (§5.2). Поле, блок «Прогон
     проверяющего» и отметка вердикта внесены коммитом проверяющей сессии. -->
**asserts_reviewed_by:** n/a (класс S — дайджест утверждений обязателен для M/L)
**CI run:** n/a reason=CI не развёрнут, волна В1; вместо ссылки — прогон в чистом клоне ниже
**Commit:** `30a3b55` — итоговое дерево с подписью владельца; прогон проверяющего — в блоке «Прогон проверяющего в чистом клоне»

## Прогон в чистом клоне

Локальные гейты обходятся (`commit -n`, `STRICT=0`), поэтому доказательство —
прогон на склонированном состоянии, а не в рабочем дереве (§10.4). База диффа в
клоне — `origin/main`: локальной ветки `main` в свежем клоне нет, и с
`--diff-base main` проверки по диффу пропускаются молча (см. приёмку, «где
процедура подвела»).

Прогон на коммите `b43cf70` (до подписи владельца):

```
$ git clone -b bootstrap/contour-b0 <репозиторий> <клон> && cd <клон>
$ python3 scripts/delivery_check.py --diff-base origin/main
breakers: kind=bootstrap — объём не мерится (§3.4): развёртывание контура не режется на части, его DoD — §7.3 + приёмка §6
наблюдение §13.1: поставок в архиве 0, окон со сроком 0, истекших без observed.md 0
WARNING: необратимое без человека: объявлено необратимое без человека в цепочке (…), подписи нет или она не сходится (§3.4a). …
WARNING: ci-oracles: weak — local gates are bypassable (§10.4); Verifier must attach a clean-clone run to verify-report.md
delivery_check: 0 error(s), 2 warning(s)
exit=0
```

- **WARNING «необратимое без человека»** — объявлены четыре поверхности, подписи ещё нет:
  её ставит владелец ключом из анклава (`bash scripts/sign_irreversible.sh`). На
  handoff это стало бы ошибкой — так и задумано (§3.4a).
- **WARNING «ci-oracles: weak»** — CI нет до волны В1; вместо ссылки на прогон CI —
  этот прогон в чистом клоне.
- **Строка `breakers:`** — у `kind: bootstrap` объём не мерится (§3.4), это не находка.

## Прогон проверяющего в чистом клоне

Проверяющий — другая сессия и другая модель, чем исполнитель. Клон сделан
проверяющим отдельно от рабочего дерева исполнителя, 2026-11-18. Итоговый прогон —
на коммите `30a3b55`, уже с подписью владельца:

```
$ git clone <репозиторий> <клон> && cd <клон> && git checkout bootstrap/contour-b0
$ python3 scripts/delivery_check.py --diff-base origin/main
breakers: kind=bootstrap — объём не мерится (§3.4): развёртывание контура не режется на части, его DoD — §7.3 + приёмка §6
наблюдение §13.1: поставок в архиве 0, окон со сроком 0, истекших без observed.md 0
WARNING: ci-oracles: weak — local gates are bypassable (§10.4); Verifier must attach a clean-clone run to verify-report.md
delivery_check: 0 error(s), 1 warning(s)
exit=0
$ bash scripts/check_irreversible_signature.sh
подпись сходится с объявлением: …
exit=0
```

Что проверяющий сверил сам, не по отчёту исполнителя:

- **Состав.** В git 33 файла. Текстов канонов и локальных папок проекта — 0 в
  дереве и 0 в истории всех веток.
- **Механика из канона.** 15 файлов `scripts/` побайтно равны payload, который
  проверяющий извлёк из канонов той же ревизии сам: 15 из 15.
- **Тексты канонов.** Семь локальных копий равны источнику по sha256: 7 из 7; в
  источнике 0 изменений.
- **Вариант D.** `extract_payload.py --check-local .` в рабочей копии — 0 и 0.
  Контроль: одноразовая копия без строк игнора и с текстами внутри — «без игнора 7»,
  код выхода 1, `git add -A --dry-run` взял бы 7 файлов. Копия удалена.
- **Самопроверка канонов.** `stack_selftest.py` — 124 блока, 51 пропущен с причиной,
  0 сбоев.
- **Подпись необратимого.** До подписи сверка красная (exit 1), после — зелёная
  (exit 0). Подписанный текст побайтно равен строке `irreversible_surfaces` в STATUS.
- **Публичность.** Имён людей, почт, доменов и признаков секретов вне `scripts/` нет.
- **DoD §7.3.** Закрыт или объявлен: CI и защита ветки — `ci-oracles: weak` до
  волны В1; гейты формы и раскладка путей — n/a до волны В1.

**Находка проверяющего для канона.** На `phase: handoff` гейт ищет в этом отчёте
слова `READY FOR HANDOFF` и находит их в неотмеченной строке шаблона: отсутствие
вердикта он не ловит. Опыт: STATUS с `phase: handoff` при трёх пустых отметках
вердикта — в выводе гейта одна ошибка, про блок метрик, и ни слова про вердикт.
Пока это так, вердикт держится порядком работы: его вносит проверяющая сессия
своим коммитом, и в main перематывается только дерево с этим коммитом.

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
- [x] READY FOR HANDOFF
- [ ] NEED CONVERGE (new tasks)
- [ ] BLOCKED

Вердикт поставил `agent:lead-session` 2026-11-18 по дереву `30a3b55` и своему
прогону выше. Открытых замечаний к волне В0 нет; оси ②, ③ и ⑤ придут своими волнами.

<!-- Вердикт ставит проверяющий, не Builder (§5.2). -->

## Harness metrics (this shipment)

<!-- generated by scripts/delivery_metrics.py --base main -->

| Metric | Value |
|---|---|
| files_touched / loc_diff | 19 code (+12 process docs) / +4416/-0 (net +4416) |
| commits | 7 |
| time_to_accepted_spec | n/a (no spec.md in history — class S?) |
| rework_after_done | 0 (handoff not declared yet) |
| harness_hardened | yes — .claude/settings.json, delivery/evals/smoke/README.md, scripts/delivery_check.py |
| implement_retries | 2 — строка решения о наблюдателе подписи правилась дважды по предупреждению «без цены» (§12.2a) |
| verify_fails_before_green | 1 — первый прогон в клоне на `dcefaa1`: ошибка «Verifier not filled» (в отчёте стояла строка шаблона) |
| est_token_or_cost | n/a — работа по подписке, расход сессии не измерялся |

MANUAL-поля заполняет агент/человек на handoff. Если `verify_fails_before_green >= 2` при `harness_hardened: no` — по §9.2 добавь oracle/breaker/hook в этой же поставке.
