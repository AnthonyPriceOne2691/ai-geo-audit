# Verify report

**Date:** 2026-11-19
**Verifier:** agent:lead-session
**asserts_reviewed_by:** human:owner at=2026-11-19T12:29Z
<!-- Подпись просил проверяющий после вердикта READY FOR HANDOFF (`85293dd`) и внёс сам: слово
     записывает тот, кто его слышал. Владельцу в 12:23Z показано, что утверждают тесты, группами:
     цвета одни на всё — каждая роль библиотеки компонентов ведёт на свой токен темы, таблица
     ролей закреплена целиком, контраст текста считается расчётом по 29 парам в каждой теме,
     худшая пара — 4,97 при норме 4,5; шрифт — в сборке ровно два набора знаков, лишний или
     пропавший набор красный; движение — при «меньше движения» переходы обнуляются; правила
     интерфейса и токены сверяются в обе стороны. Три утверждения дайджеста без примера этой
     спеки названы поимённо: «проверено 7 файлов» в тесте проверки сборки; строка заглушки
     «Сейчас здесь проверка сборки и образцы вида» (его слово 11:44Z); слова «скоро» на заглушке
     нет. Названы найденный дефект (подпись главной кнопки 1,99 : 1 в тёмной теме, исправлено)
     и невошедшее: переключатель темы и серые линии таблицы — следующей малой поставкой. Пути
     трёх снимков даны ему для просмотра. Счёт дайджеста — 52 утверждения, без примера — 3.
     Ответ владельца ведущей сессии в 12:29Z, дословно: «да на всё» — на список из семи пунктов,
     повторённый ему целиком в 12:28Z; седьмой пункт — эта подпись и публикация поставки в main.
     Внутри групп «шрифт» и «цвета» шли ожидания F6 и F12: их проверки идут через помощника
     оракула сборки, и дайджест их не считает. Проверяющий назвал это владельцу следующим
     сообщением после слова. -->
**CI run:** n/a reason=CI не развёрнут, волна В1; вместо ссылки — прогон в чистом клоне ниже
**Commit:** см. блок «Прогон в чистом клоне»

## Прогон в чистом клоне

Локальные гейты обходятся, поэтому доказательство — прогон на склонированном состоянии (§10.4).
База диффа — `origin/main` клона (`2bdf4d9`, поставка Ч1.2в).

Итоговый прогон — на `0942eed`, моим прогоном с 2026-11-19T12:15:03Z по 12:19:08Z. Клон лежал во
временной папке, команды в нём шли подоболочкой (L20), после прогона клон удалён. Вывод ниже —
дословный, только путь клона заменён на `<клон>`.

```
$ git clone -b ch1-04b-front-theme <репозиторий> <клон> && cd <клон>
$ git log --format='%h %p %s' -3 && git merge-base HEAD origin/main && git diff --stat 1e8e676 HEAD
0942eed 828d1b7 docs(delivery): Ч1.4б — фаза verify, отчёт сверки, цена двух решений
828d1b7 1e8e676 test(frontend): Ч1.4б — радио и контурный флажок в тесте темы F11
1e8e676 7e15ed7 2bdf4d9 merge: main (Ч1.2в) в ветку Ч1.4б перед вердиктом
2bdf4d968c5dad788b6c0bfc4ae2e97296f25a9a
 delivery/active/STATUS.md         |  13 +-
 delivery/active/decisions.md      |   8 +-
 delivery/active/eval-smoke.md     |   7 +-
 delivery/active/tasks.md          |   6 +-
 delivery/active/verify-report.md  | 273 ++++++++++++++++++++++++++++++++++++++
 frontend/src/theme/theme.test.tsx |   9 +-
 6 files changed, 301 insertions(+), 15 deletions(-)
$ python3 scripts/delivery_check.py --diff-base origin/main; echo "exit=$?"
breakers: files=16 net_loc=1175 (+1193/-18), excluded=10 (delivery/|knowledge/|scripts/lint/|scripts/merge_guard.sh|scripts/delivery_|scripts/okf_|.claude/|docs/canon/|.pre-commit-config.yaml|.github/workflows/|.gitlab-ci.yml|scripts/check_irreversible_signature.sh|scripts/sign_irreversible.sh|stack_selftest.py|selftest_sizes.py|extract_payload.py|scripts/stack_selftest.py|scripts/selftest_sizes.py|scripts/extract_payload.py|package-lock.json|npm-shrinkwrap.json|yarn.lock|pnpm-lock.yaml|bun.lockb|poetry.lock|uv.lock|Pipfile.lock|Cargo.lock|go.sum|Gemfile.lock|composer.lock|Podfile.lock), limits={'max_files_touched': 25, 'max_loc_diff': 1200, 'max_runtime_paths': 1, 'max_unsigned_irreversible': 0}
наблюдение §13.1: поставок в архиве 6, окон со сроком 6, истекших без observed.md 0
WARNING: class M: asserts_reviewed_by deferred — ещё никем не подписано (§2.2b); долг закрывается до handoff
WARNING: acceptance-примеры без ссылки в тестах/eval-smoke: F13, F19 (§3.1d) — пометь тест id примера в комментарии или в начале докстринга; строка, целиком равная id, и %-код ссылкой не считаются
WARNING: class M: ни одного реляционного оракула с генератором входов (§6.5) — в тестах не найдено: `@given` (hypothesis); `fc.property` (fast-check). Отношение на фиксированном корпусе (паритет, метаморфное без генератора) сюда не идёт: входы в нём выбрал автор. Инвариант, round-trip, идемпотентность, метаморфное отношение или differential: в них нет ожидаемого значения, поэтому в них нельзя спрятать неверное ожидание. Один инвариант обычно ловит больше десяти тестов-значений, потому что раннер перебирает входы, о которых автор не думал. Проверь заодно, что mutation-гейт у тебя не пропускается: иначе слабое свойство (`assert result is not None`) пройдёт
WARNING: ci-oracles: weak — local gates are bypassable (§10.4); Verifier must attach a clean-clone run to verify-report.md
delivery_check: 0 error(s), 4 warning(s)
exit=0
$ cd frontend && node --version && npm --version && npm ci && npm run check; echo "exit=$?"
v24.12.0
11.11.1
npm warn deprecated whatwg-encoding@3.1.1: Use @exodus/bytes instead for a more spec-conformant and faster implementation
npm warn deprecated eslint@9.39.5: This version is no longer supported. Please see https://eslint.org/version-support for other options.

added 409 packages, and audited 410 packages in 8s

145 packages are looking for funding
  run `npm fund` for details

7 moderate severity vulnerabilities

To address issues that do not require attention, run:
  npm audit fix

To address all issues, run:
  npm audit fix --force

Run `npm audit` for details.

> ai-geo-audit-frontend@0.1.0 check
> npm run lint && npm run format:check && npm run typecheck && npm test && npm run build && npm run check:dist


> ai-geo-audit-frontend@0.1.0 lint
> eslint .


> ai-geo-audit-frontend@0.1.0 format:check
> prettier --check .

Checking formatting...
All matched files use Prettier code style!

> ai-geo-audit-frontend@0.1.0 typecheck
> tsc --noEmit


> ai-geo-audit-frontend@0.1.0 test
> vitest run


 RUN  v5.0.3 <клон>/frontend


 Test Files  4 passed (4)
      Tests  72 passed (72)
   Start at  15:15:16
   Duration  1.41s (tests 51%, import 22%, environment 14%, setup 9%, transform 4%)


> ai-geo-audit-frontend@0.1.0 build
> vite build

vite v7.3.7 building client environment for production...
transforming...
✓ 755 modules transformed.
rendering chunks...
computing gzip size...
dist/index.html                                              0.40 kB │ gzip:  0.27 kB
dist/assets/geologica-cyrillic-wght-normal-DbeVG3Ps.woff2   16.43 kB
dist/assets/geologica-latin-wght-normal-BBL8hQxZ.woff2      24.94 kB
dist/assets/index-DZcS4GCz.css                             205.79 kB │ gzip: 30.75 kB
dist/assets/index-dl4BN3-e.js                              314.26 kB │ gzip: 96.59 kB
✓ built in 726ms

> ai-geo-audit-frontend@0.1.0 check:dist
> node scripts/check-dist.mjs

проверено 5 файлов
exit=0
$ git status --porcelain | wc -l
       0
```

- **Первый прогон** — на `1e8e676`, субагентом исполнителя, с 11:53:25Z по 12:03:14Z. Гейт — 0
  ошибок и 3 предупреждения, 16 файлов, 1168 строк; цепочка — 72 теста, «проверено 5 файлов»;
  на нём же сняты снимки и замерена ширина. Отчёта в том клоне ещё не было, а фаза в
  `STATUS.md` была `implement`, поэтому проверок фазы verify гейт там не делал. После него —
  правка теста по двум выжившим порчам проверяющего (`828d1b7`) и документы сверки (`0942eed`).
  Сборка на `0942eed` дала те же имена файлов с хешем, что на `1e8e676`: `index-DZcS4GCz.css`,
  `index-dl4BN3-e.js`, — страница та же.
- **Гейт в рабочей копии до коммита документов** дал ещё два предупреждения §12.2a: у двух записей
  `decisions.md` цена была без числа и без ссылки на код. Цена названа, на `0942eed` их нет.
- **WARNING «asserts_reviewed_by deferred»** — подпись владельца под утверждениями просит и вносит
  проверяющий после своего вердикта.
- **WARNING «F13, F19 без ссылки»** — примеры среза 2. Они ушли по шву в поставку «Схема и цвета
  компонентов» с теми же номерами («Spec coverage gaps»).
- **WARNING «реляционный оракул»** — долг на волну В1, как у прошлых поставок.
- **WARNING «ci-oracles: weak»** — CI нет до волны В1. Вместо ссылки — этот прогон.
- Тесты по файлам — оракул сборки 33, тема 24, инструменты 10, заглушка 5; пропусков 0. Правка
  `828d1b7` добавила утверждения в тест темы, а не тесты.
- Сборка — 5 файлов: `index.html`, JS, CSS и два файла шрифта. `npm ci` предупреждает о снятых с
  поддержки `eslint` 9.39.5 и `whatwg-encoding` 3.1.1, как в Ч1.4а.

### Возраст версий и `npm audit`

Тот же клон, с 12:15:31Z по 12:19:08Z. Инструмент возраста — `docs/deliveries/front-tools/
lock_age.py`, вне git; граница — 14 суток до прогона.

```
$ python3 -I lock_age.py <клон>/frontend/package-lock.json 2026-11-05T12:15:31Z
сверено пар: 457, имён: 442, моложе 2026-11-05T12:15:31Z: 0
exit=0
$ cd frontend && npm audit; echo "exit=$?"
# npm audit report

minimatch  7.0.0 - 11.0.0
Severity: moderate
minimatch has potential denial of service via globparts amplification - https://github.com/advisories/GHSA-g3hv-75wg-m249
fix available via `npm audit fix --force`
Will install typescript-eslint@8.73.0, which is outside the stated dependency range
node_modules/@typescript-eslint/typescript-estree/node_modules/minimatch
  @typescript-eslint/typescript-estree  6.16.0 - 8.72.2
  Depends on vulnerable versions of minimatch
  node_modules/@typescript-eslint/typescript-estree
    @typescript-eslint/parser  6.16.0 - 8.72.2
    Depends on vulnerable versions of @typescript-eslint/typescript-estree
    node_modules/@typescript-eslint/parser
      typescript-eslint  <=8.72.2
      Depends on vulnerable versions of @typescript-eslint/eslint-plugin
      Depends on vulnerable versions of @typescript-eslint/parser
      Depends on vulnerable versions of @typescript-eslint/typescript-estree
      Depends on vulnerable versions of @typescript-eslint/utils
      node_modules/typescript-eslint
    @typescript-eslint/type-utils  6.16.0 - 8.72.2
    Depends on vulnerable versions of @typescript-eslint/typescript-estree
    Depends on vulnerable versions of @typescript-eslint/utils
    node_modules/@typescript-eslint/type-utils
    @typescript-eslint/utils  6.16.0 - 8.72.2
    Depends on vulnerable versions of @typescript-eslint/typescript-estree
    node_modules/@typescript-eslint/utils
      @typescript-eslint/eslint-plugin  6.16.0 - 8.72.2
      Depends on vulnerable versions of @typescript-eslint/type-utils
      Depends on vulnerable versions of @typescript-eslint/utils
      node_modules/@typescript-eslint/eslint-plugin

7 moderate severity vulnerabilities

To address issues that do not require attention, run:
  npm audit fix

To address all issues, run:
  npm audit fix --force
exit=1
$ npm audit --omit=dev; echo "exit=$?"
found 0 vulnerabilities
exit=0
```

- **Возраст** — по реестру npm: из 457 пар lock (имён 442) моложе 14 суток — 0.
- **`npm audit`** — 7 находок средней тяжести, высокой и критической 0; без dev-зависимостей — 0.
  Причина одна: GHSA-g3hv-75wg-m249, `minimatch` 10.2.6 внутри `typescript-eslint` 8.70.0.
  Исправленный `typescript-eslint` 8.73.0 вышел 2026-11-16, правило 14 суток пустит его с
  2026-11-30 — это срок решения проверяющего в Ч1.4а.

## Дайджест утверждений

`assert_digest.sh` взят из полезной нагрузки канона, положен в клон и после прогона убран (CQG в
репозитории ещё нет). Маски тестов — `*test*.ts *test*.tsx *test*.mjs` (L25). База —
`origin/main`. Прогон на `0942eed`, 2026-11-19T12:15:31Z, вывод — дословно:

````text
$ BASE=origin/main LINT_TEST_GLOB="*test*.ts *test*.tsx *test*.mjs" bash scripts/lint/assert_digest.sh; echo "exit=$?"
## Assertion digest (ревью ожиданий, не кода)

База: `origin/main` · сгенерировано `assert_digest.sh`

Новых/изменённых утверждений: **52**, из них без ссылки на пример спеки:
**3**. Вопрос к каждому непривязанному один: **откуда взято ожидаемое
значение — из спеки или придумано под реализацию?**

```
-	expect(out.trim()).toBe('проверено 7 файлов');
-	expect(screen.getByText('Сейчас здесь проверка сборки и образцы вида')).toBeInTheDocument();
-	expect(document.body.textContent).not.toMatch(/скоро/i);
F20	expect(paper, 'заголовок образцов не на карточке').not.toBeNull();
F20	expect(card.getByText(samples.text).style.color).toBe('');
F20	expect(card.getByText(samples.muted).style.color).toBe('var(--mantine-color-dimmed)');
F20	expect(link.getAttribute('href')).toBe(`#${screen.getByRole('table').id}`);
F20	expect(statuses).toEqual([
F20	expect(cells.map((cell) => cell.style.textAlign)).toEqual(Array(12).fill('center'));
F10	expect(textColorVar('Проверено страниц')).toBe('currentColor');
F10	expect(textColorVar('Отказ')).toBe(cssVar('crit'));
F10	expect(screen.getByText('Подпись колонки').style.color).toBe('var(--mantine-color-dimmed)');
F11	expect(Object.keys(colors.light)).toHaveLength(20);
F11	expect(Object.keys(colors.dark).sort()).toEqual(Object.keys(colors.light).sort());
F11	expect(PAIRS).toHaveLength(29);
F11	expect(unknown).toEqual([]);
F11	expect(low).toEqual([]);
F11	expect(outside.map(([role, value]) => `${role}: ${value}`)).toEqual([]);
F11	expect(resolved[scheme], `схема ${scheme}`).toEqual(Object.fromEntries(want));
F11	expect(Object.values(ROLE_TABLE).filter((role) => !(role in colors.light))).toEqual([]);
F11	expect(low).toEqual([]);
F11	expect(variantColorResolver(input('filled')).color).toBe(cssVar('onPrimary'));
F11	expect(variantColorResolver(one)).toEqual(defaultVariantColorsResolver(one));
F11	expect(button.style.getPropertyValue('--button-color')).toBe(cssVar('onPrimary'));
F11	expect(viaTheme(read('.mantine-Checkbox-root', '--checkbox-color'))).toBe(cssVar('primary'));
F11	expect(read('.mantine-Checkbox-root', '--checkbox-icon-color')).toBe(cssVar('onPrimary'));
F11	expect(read('.mantine-Radio-root', '--radio-icon-color')).toBe(cssVar('onPrimary'));
F11	expect((outline as HTMLElement).style.getPropertyValue('--checkbox-icon-color')).toBe('');
F11	expect(viaTheme(read('.mantine-Tabs-root', '--tabs-color'))).toBe(cssVar('primary'));
F11	expect(text).toContain(`--mantine-color-body: ${cssVar('surface')}`);
F11	expect(text).toContain(`--mantine-color-${P}-filled: ${cssVar('primary')}`);
F11	expect(roles).toContain('--mantine-primary-color-filled-hover');
F11	expect(bare, `схема ${scheme}: роли Mantine без токена`).toEqual([]);
F14	expect(seen.at(-1)).toEqual({});
F14	expect(durations.length).toBeGreaterThan(0);
F14	expect(after).toEqual(Object.fromEntries(durations.map((name) => [name, '0ms'])));
F15	expect(durations.sort()).toEqual(['120ms', '180ms', '260ms']);
F15	expect(curves.map(byValue)).toEqual([byValue('cubic-bezier(.2, .8, .2, 1)')]);
F15	expect([root['--audit-radius-card'], root['--audit-radius-control']]).toEqual(['16px', '10px']);
F15	expect(merged.radius[String(merged.defaultRadius)]).toBe(cssVar('radiusControl'));
F15	expect(merged.radius[step]).toBe(cssVar('radiusCard'));
F15	expect(steps).toEqual(new Set([cssVar('radiusCard'), cssVar('radiusControl')]));
F15	expect(root['--audit-font']).toMatch(/^'Geologica Variable', /);
F15	expect([merged.fontFamily, merged.headings.fontFamily]).toEqual([
F15	expect(page['font-family']).toBe(cssVar('font'));
F15	expect(page['font-variant-numeric']).toBe('tabular-nums');
F15	expect([root['--audit-workspace'], root['--audit-gutter']]).toEqual(['1360px', '28px']);
F15	expect(area.style.getPropertyValue('--container-size')).toBe(cssVar('workspace'));
F15	expect(area.style.paddingInline).toBe(cssVar('gutter'));
F18	expect(cells.map((cell) => cell.style.textAlign)).toEqual(Array(4).fill('center'));
F15	expect(drift(rules, tokenCells())).toEqual([]);
F15	expect(numbers.map(byValue)).toEqual(
```

Примеры спеки (`delivery/active/spec.md`): A23 F10 F11 F12 F13 F14 F15 F18 F19 F20 F6.

Привязаны к примерам: **F10 F11 F14 F15 F18 F20**. Остальные 3 — нет.

Читать нужно **только строки с `-` в первой колонке**: их ожидание
ничем не подписано. Подпись: `asserts_reviewed_by: human:… at=…`.

asserts_without_example: 3
exit=0
$ rm scripts/lint/assert_digest.sh && rmdir scripts/lint && git status --porcelain | wc -l
0
````

- Новых и изменённых утверждений — 52, без ссылки на пример этой спеки — 3.
- По примерам: F11 — 21, F15 — 15, F20 — 6, F10 — 3, F14 — 3, F18 — 1. Два новых у F11 — радио
  и контурный флажок (`828d1b7`).
- Без ссылки — три строки. Они стоят под номерами примеров Ч1.4а, которых в таблице этой спеки
  нет. Откуда взято ожидание:
  - `frontend/scripts/check-dist.test.mjs:128`, F5: «проверено 7 файлов» вместо пяти. В образец
    сборки теста легли два файла шрифта (F6). Строка F5 подписана со словами «проверено N
    файлов», и N здесь — счёт файлов образца;
  - `frontend/src/StubPage.test.tsx:49`, F16: строка-факт «Сейчас здесь проверка сборки и
    образцы вида». Это правка подписанной строки по слову владельца 2026-11-19T11:44Z, в спеке
    она записана абзацем, а не строкой таблицы;
  - `frontend/src/StubPage.test.tsx:50`, F16: «скоро» на странице нет — то же ожидание, что было
    в F16 на main. Его копию в тесте F20 git выровнял как контекст, и в счёт попала строка F16.
- F6 и F12 в счёте нет. Их проверки — три вызова помощника `expectRed` и `expectRedAt` (он помечен
  F5 в Ч1.4а), а дайджест ищет `expect(`. Ожидания — строки причин оракула в таблицах случаев:
  - F12 — «в CSS сборки нет правила страницы — фон var(--audit-bg), текст var(--audit-ink)», «нет
    --audit-bg схемы dark из токенов», «нет --audit-ink схемы dark из токенов»;
  - F6 — «шрифт не из наборов латиницы и кириллицы» на файле греческого набора и на файле
    `.ttf`, «нет файла шрифта с кириллицей», «нет файла шрифта с латиницей», «чужой узел
    fonts.gstatic.com».
- A23 утверждений не имеет: это скан, его вывод — в «Product oracles».

asserts_without_example: 3

## Ревью рисковых мест

- **Единый источник токенов** — `frontend/src/theme/tokens.css`: цвета двух схем, скругления,
  шрифт, движение, рабочая область, правило страницы. Тема ссылается на токены переменными
  `var(--audit-…)`, тесты читают файл тем же разбором `tokens.ts`. Разбор `tokens.css?raw` в
  теме отвергнут: Vitest 5.0.3 отдаёт любой `*.css?raw` пустой строкой (запись в `decisions.md`).
- **Цвета библиотеки.** Роли схемы Mantine вне палитр и восемь ролей палитры основного цвета (по
  `theme.primaryColor`) — на токенах в обеих схемах; таблица ролей целиком — в тесте (L23).
  Библиотеке оставлены поимённо, с причиной: `--mantine-color-scheme` и три роли выключенного.
  Подпись `filled` основного цвета — `onPrimary` своим `variantColorResolver` поверх умолчания
  Mantine; прочие виды и цвета выходят из него такими же, как из умолчания, — тест. Значок
  флажка и радио на заливке — `onPrimary` через `vars`. У контурного вида под значком
  поверхность, и цвет значку даёт правило Mantine. Тест держит обе стороны: радио и флажок на
  заливке — `onPrimary`, у контурного флажка переменной значка от темы нет. Путь через
  `--mantine-color-white` по схемам отвергнут: белый библиотеки стоит в 57 правилах Mantine, и в
  тёмной схеме это и светлый текст на тёмных поверхностях. Находки ранней приёмки проверяющего на
  `5cd724a` — две, закрыты `75d366d` (тесты красными) и `a947725`; на `1e8e676` — две выжившие
  порчи, закрыты правкой теста (раздел «Behavior oracles»). Чего тема ещё не красит — в «Spec
  coverage gaps».
- **Контраст** — расчётом над токенами: 29 пар на схему, худшая пара текста — 4,97 в светлой и
  5,29 в тёмной; все 20 цветов обеих схем — в охвате sRGB. Плюс пять пар ролей Mantine, которыми
  компоненты красятся на деле: текст, ошибка и рамка на поверхности, подпись на заливке, текст
  светлого вида на его подложке.
- **OKLCH без запасных цветов** — решение проверяющего 2026-11-19T11:09Z. Нижняя граница
  браузеров — «Chrome и Edge 111+, Firefox 121+, Safari 15.4+»: `oklch()`, `:has()` и `dvh` —
  по `caniuse-lite` 1.0.30001820 из пакетов и поиском по собранным CSS и JS. Строка — в
  `frontend/UI_RULES.md` и README.
- **Шрифт** — `@fontsource-variable/geologica` 5.3.0 (вышел 2026-07-19, OFL-1.1), поставлен под
  `min-release-age=14`. Свои `@font-face` на два файла: CSS пакета объявляет шесть наборов, и в
  сборку ушли бы все шесть. Оракул F6 — красный на лишнем и на пропавшем наборе.
- **Оракул сборки, F12 (половина).** Фон и цвет текста обеих схем ищутся в CSS сборки и
  сверяются с файлом токенов по значению: esbuild пишет `0.020` как `.02` и снимает кавычки в
  селекторе.
- **Щит цвета текста** — без условия по пропсу: переменная пропса и так ложится позже стилей
  темы (порча проверяющего T13 была равносильна коду). На этот порядок Mantine опирается тест F10
  «с пропсом — цвет пропса».
- **Правка подписанной строки F16 Ч1.4а** — по слову владельца 2026-11-19T11:44Z: «Сейчас здесь
  проверка сборки и образцы вида». Метки статусов в образцах — словами ТЗ: «ОК», «Внимание»,
  «Ошибка» (строка «Статусы»).
- **Перенос и слияние.** Ветка перенесена с `861384c` на `7d66439` с `rebase -X no-renames`. Main
  с Ч1.2в влит слиянием `1e8e676`: дерево `frontend/` не изменилось, README собрался без
  конфликта. На вершине каталог `delivery/archive/2026-11-19-ch1-04a-front-build/`, 9 файлов,
  побайтно равен `7d66439` и main. Вся папка `delivery/archive/` вместе с `INDEX.md` побайтно
  равна main `2bdf4d9`: архив ветка не трогает. Файлы подписи необратимого в `active/` — те же
  байты, что в архиве 4а.
- **Объём** — 1168 строк по гейту на `a947725` и на `1e8e676` при пределе 1200 и пороге 1100.
  Проверяющему названо до сдачи, принято как итоговое (2026-11-19T11:47Z). Правка теста по двум
  порчам проверяющего — плюс 7, итог на `0942eed` — 1175: проверяющий разрешил её до 1190. Срез 2
  «Схема» ушёл по шву: после среза 4 — 1024, больше 860.

## Исполнение рисковых путей

- `frontend/src/` — исполнено снимками, Chrome 157 без окна. Сборка — из `npm run build` клона,
  `vite preview` на `127.0.0.1`, порт — свободный от системы. at=2026-11-19T12:00Z

  | Снимок | Что видно |
  |---|---|
  | 1440 × 900, светлая и тёмная | имя «AI GEO Audit» заголовком, строка «Сейчас здесь проверка сборки и образцы вида»; карточка «Образцы вида» — основной текст, приглушённый текст, ссылка «Ссылка к таблице ниже»; таблица из трёх строк по центру — «Главная», «Блог», «Контакты»; статусы «ОК», «Внимание», «Ошибка» на подложках; рабочая область — 1360 px, поля по 28 px |
  | 1280 × 900, светлая и тёмная | то же, рабочая область во всю ширину, поля по 28 px |
  | 390 × 844 через рамку шириной 390, светлая и тёмная | то же; текст переносится, таблица помещается |

  Переключателя схемы на снимках нет: он ушёл по шву в поставку «Схема и цвета компонентов».
  Строка F20 в таблице спеки по-прежнему называет переключатель, и спека отсылает эту половину
  F20 туда же. Линии таблицы — серые библиотеки, в тёмной схеме это видно глазом; у шапки таблицы
  нет подложки `sunken`. Это утечки из «Spec coverage gaps», они идут в ту же поставку.

  Ширина — протоколом DevTools, 8 замеров: 390 обычным и мобильным видом, 1280 и 1440, обе
  схемы. Во всех `scrollWidth` равен `clientWidth`, правый край каждого элемента — в пределах
  окна. Атрибут схемы и фон страницы — схемы снимка; элементов управления на странице 0.

  Снимки — `docs/deliveries/ch1-04b-shots/`, вне git, инструмент —
  `docs/deliveries/front-tools/shots.sh`. Код страницы после снимков не менялся: после `1e8e676`
  правился только тест темы, и сборка на `0942eed` — с теми же именами файлов с хешем.

## Shape oracles
- [x] n/a — CQG не развёрнут (волна В1). Руками: `npm run lint` (`eslint .`) — 0 ошибок,
      0 предупреждений; `prettier --check .` — чисто; строгие флаги TypeScript — по CQG §2.2;
      строки не длиннее 100 знаков.

## Behavior oracles
- [x] PASS — `npm run check` в чистом клоне: 72 теста в четырёх файлах, пропусков нет. У каждого
      теста поставки в описании номер примера.
- [x] PASS — порчи проверяющего, `docs/deliveries/review-tools/mutants_ch104b.py`:
      - на `5cd724a` — 31, убиты 26. Выжили T13, T16, T17, T18 и T21 — закрыто `75d366d` и
        `a947725`;
      - на итоговом коде `1e8e676`, моим прогоном в свежем клоне, с 2026-11-19T12:07:12Z по
        12:07:58Z, — убиты 29 из 31: 25 тестами, 4 сборкой и оракулом (T23–T26). T12 и T13 не
        легли: щит теперь без условия по пропсу;
      - T12 в новой форме, «щит убран», — красный F10, в 12:08:21Z, `vitest run --no-cache`.
        Файл возвращён, `cmp` совпал;
      - дерево после порч чистое;
      - ранняя приёмка проверяющего на `1e8e676`, скрипт под исправленную тему: 41 порча, убиты 39
        (тестами 35, сборкой 4). Выжили две. P7 — у `Radio` в теме нет `vars`: радио в тесте не
        было. P8 — из `iconOnFill` снято условие, и значок `onPrimary` получает и контурный
        флажок, у которого под значком поверхность. Закрыто правкой теста F11 «кнопка, флажок,
        радио и вкладки»: в него вошли радио и контурный флажок. P7 и P8 моим прогоном на
        правке, `vitest run --no-cache`, — красные, каждая на своём утверждении (строки 234 и
        237 теста темы). Файл после каждой возвращён, `cmp` совпал.
- [x] PASS — порчи исполнителя, по одной, без кэша, с возвратом файла и сверкой `cmp`:
      - срез 1: щит снят (F10); `faint` светлее на ступень — красный с именем пары и числом
        (F11); без `respectReducedMotion` и без правила «меньше движения» (F14); без центровки
        таблицы (F18); проверка фона и текста схем снята с оракула (F12);
      - срез 3: тесты F6 красные до кода — 4 из 4;
      - срез 4: значение в таблице правил разошлось, строка токена убрана, лишняя строка, строка
        260 мс убрана (F15); статус «Ошибка» убран (F20) — плюс семь сверх списка;
      - роли Mantine: строка заливки под курсором убрана; роль ведёт на цвет палитры библиотеки;
        строка роли палитры убрана; подпись снова белая библиотеки; значок флажка библиотечный;
        подпись `onPrimary` у любого цвета; `AppProviders` без своих переменных; рамка, ошибка и
        подпись на заливке — на чужих токенах.

## Product oracles
- [x] PASS — A23: сканер слов и поясов РФ и РБ по всем 26 новым и изменённым файлам поставки.
      Это 17 файлов кода в `frontend/` и README и 9 документов в `delivery/active/`, с этим
      отчётом. Последний прогон — по рабочей копии перед коммитом отчёта, в 2026-11-19T12:21:45Z:
      совпадений 6, в коде 0.
      - Одно — в lock: две буквы названия сервиса внутри base64-строки `integrity` пакета
        `@eslint/object-schema` 2.1.7. Такие строки по A23 пропускаются; та же строка есть на
        main и была находкой Ч1.4а.
      - Пять — слова самого правила A23: в `spec.md`, `plan.md`, `tasks.md`, `eval-smoke.md` и в
        первой строке этого пункта.
      - `lang="ru"` сканер не берёт: это язык страницы.
      - Прогон субагента на `1e8e676`, только код — 17 файлов, в 11:59:08Z: одно совпадение, то
        же в lock.
- [x] PASS — eval-smoke:
  - F6, F10–F15, F18–F20 — на Node 24 `npm ci && npm run check`: шесть шагов зелёные, 72 теста,
    пропусков 0; после цепочки `git status --porcelain` пуст;
  - F6 и половина F12 — `npm run check:dist`: «проверено 5 файлов», код 0. Порчи, которые видит
    только сборка, красные в оракуле с именем файла или правила (T23–T26 выше): другой набор
    шрифта вместо кириллицы, CSS пакета шрифта целиком, файл токенов не подключён, фон страницы
    не из токена;
  - F20 — снимки и замер ширины (выше), без переключателя схемы;
  - A23 — выше.
- [ ] PASS/FAIL — `delivery/evals/smoke`: продукта, который отвечает по сети, ещё нет.

## Spec coverage gaps
- Реляционного оракула с генератором входов нет — долг на волну В1, как у прошлых поставок.
- **Срез 2 «Схема» ушёл** в поставку «Схема и цвета компонентов» сразу после этой, до пробного
  выката 25.11: скриптовая часть F12 (скрипт схемы в `<head>`, общий ключ хранилища, сбой
  хранилища), F13, F19 и переключатель схемы из F20. До неё схема следует системе силами
  библиотеки, а у тёмной системы при загрузке — вспышка светлой: блоки токенов стоят под
  атрибутом схемы, и до его появления фон и текст берутся у браузера.
- **Утечки цвета библиотеки** — в ту же поставку, до пробного выката и первого настоящего экрана:
  у `Card` свой фон (белый в светлой схеме, `dark-6` в тёмной), поэтому карточка образца сделана
  на `Paper`; линии таблицы — `gray-3` и `dark-4` библиотеки, у шапки таблицы нет `sunken`.
- Белыми библиотеки остаются бегунок тумблера, подсказка, бейдж индикатора и подпись полосы хода —
  до первого экрана с ними.
- Правило «в пропс цвета — только токены, имена палитр библиотеки не используются» записано в
  `frontend/UI_RULES.md`; тест на него — с первым экраном.
- `color-mix()`: если компонент получит цвет-переменную в пропсе цвета, граница Safari поднимется
  до 16.2 — в `caniuse-lite` этой строки нет, число по памяти.
- `npm audit` — 7 находок средней тяжести с одной причиной до 2026-11-30 (решение проверяющего,
  Ч1.4а).

## Verdict
- [x] READY FOR HANDOFF
- [ ] NEED CONVERGE (new tasks)
- [ ] BLOCKED

Вердикт проверяющего на `db0303a`, 2026-11-19T12:24Z: к сдаче. До коммита сдачи нужна подпись
владельца под утверждениями — её просит и вносит проверяющий.

Что проверил сам, в своей копии приёмки:

- код срезов 1 и 3 прочитал на ранней приёмке (`5cd724a`), срез 4 и исправление цветов — по
  разнице на `1e8e676`: `356ba3c`, `cc6d4e1`, `75d366d`, `a947725`, `7e03918`; после неё —
  `828d1b7` (только тест темы, +8/−1), `0942eed` и `db0303a` (только документы; дерево
  `frontend/` на `db0303a` то же, что на `0942eed`);
- слияние main `1e8e676` (правило двух полос): дерево `backend/` равно main `2bdf4d9`, дерево
  `frontend/` до и после слияния одно, архив поставок против main без разницы;
- таблица примеров спеки равна подписанной владельцем (`a7f322a`) побайтно — 10 строк. После
  подписи менялись только абзацы вне таблицы: шов и уход среза «Схема», OKLCH без запасных
  цветов, «Сверка с ТЗ», правка строки F16 по слову владельца 2026-11-19T11:44Z;
- сверка с ТЗ — по тексту ТЗ: метки статусов «ОК», «Внимание», «Ошибка» — слова строки «Статусы»;
  три языка ТЗ называет у отчётов, язык экрана им не задан; отступлений нет;
- гейт против `origin/main` (`2bdf4d9`) на `db0303a`, 2026-11-19T12:24:40Z, дословно, длинные
  строки обрезаны знаком «…»:

  ```
  breakers: files=16 net_loc=1175 (+1193/-18), excluded=10 (…), limits={…}
  наблюдение §13.1: поставок в архиве 6, окон со сроком 6, истекших без observed.md 0
  WARNING: class M: asserts_reviewed_by deferred — ещё никем не подписано (§2.2b); долг закр …
  WARNING: acceptance-примеры без ссылки в тестах/eval-smoke: F13, F19 (§3.1d) — пометь тест …
  WARNING: class M: ни одного реляционного оракула с генератором входов (§6.5) — в тестах не …
  WARNING: ci-oracles: weak — local gates are bypassable (§10.4); Verifier must attach a cle …
  delivery_check: 0 error(s), 4 warning(s)
  ```

- `npm ci && npm run check` на `0942eed`, Node 24: 72 теста в четырёх файлах, сборка, оракул
  сборки — «проверено 5 файлов», код 0; после цепочки `git status --porcelain` пуст;
- свои порчи (`mutants_ch104b.py`: замена в файле → весь `vitest run` → при зелёном сборка и
  оракул сборки), дерево после них чистое:
  - 31 на `5cd724a` — убиты 26, выжили T13, T16, T17, T18 и T21. Нашли дефект, а не только
    слабый тест: подпись главной кнопки в тёмной схеме шла белым библиотеки — 1,99 : 1, роли
    палитры основного цвета шли мимо токенов. Правка — `a947725`, тесты — `75d366d`; T13
    равносильна коду, щит упрощён;
  - 41 на `1e8e676` (скрипт под исправленную тему: роли палитры, подпись и значок на заливке,
    строка заглушки, таблицы правил) — убиты 39, выжили P7 и P8: точка радио и условие
    «контурный флажок без значка темы» не держались тестом. Закрыто `828d1b7`;
  - 41 на `0942eed` — убиты 41: тестами 37, сборкой и оракулом 4;
- снимки своей рукой, Chrome без окна, сборка `1e8e676` (код страницы после неё не менялся):
  1440 × 900, 1280 × 900 и 390 × 844 через рамку, обе схемы. Фон, карточки, шрифт, ссылка,
  таблица по центру, статусы словом на подложке — как в образце владельца; на 390 страница не
  шире окна, подписи целиком. Линии таблицы — серые библиотеки: утечка из «Spec coverage gaps»;
- пакеты: против main в lock добавлена одна запись — `@fontsource-variable/geologica` 5.3.0;
  контрольная сумма lock равна сумме реестра npm, версия вышла 2026-07-19, лицензия OFL-1.1.
  `npm audit` — 7 находок средней тяжести, высокой и критической 0; без dev-зависимостей — 0;
- скан правки и сообщений 18 коммитов: имён людей, почт, доменов, путей машины, секретов, слов
  и поясов РФ и РБ нет. Совпадения скана — только служебная почта автора коммитов.

Числа отчёта исполнителя с этими прогонами совпадают: 72 теста, 5 файлов сборки, 1175 строк по
счёту гейта. Блок «Прогон в чистом клоне» исполнитель снял заново на `0942eed` (`db0303a`),
дословной вставкой; мой прогон той же вершины дал те же числа.

Три утверждения дайджеста не привязаны к строкам примеров этой спеки — проверяющий показывает
их владельцу при подписи: «проверено 7 файлов» в тесте оракула (в образец сборки легли два
файла шрифта); строка заглушки «Сейчас здесь проверка сборки и образцы вида» (его слово
11:44Z); слова «скоро» на заглушке нет (ожидание F16 из Ч1.4а).

Открытое названо в «Spec coverage gaps» и идёт в поставку «Схема и цвета компонентов»: выбор
схемы и переключатель (F13, F19, половины F12 и F20), вспышка светлой схемы при тёмной системе,
свой фон `Card`, линии и шапка таблицы. Бегунок тумблера, подсказка, бейдж индикатора и подпись
полосы хода — до первого экрана с ними. Контурного радио в тесте нет: условие в `iconOnFill`
общее с флажком, его держит контурный флажок. Свойство с генератором — волна В1.

## Harness metrics (this shipment)

<!-- generated by scripts/delivery_metrics.py --base main -->

| Metric | Value |
|---|---|
| files_touched / loc_diff | 17 code (+9 process docs) / +1203/-18 (net +1185); гейт без lock — 16 файлов, 1175 строк |
| commits | 23 — с вердиктом и подписью проверяющего, двумя слияниями main и коммитом сдачи |
| time_to_accepted_spec | 0.0h — спеку подписал владелец до открытия ветки (08:59Z) |
| rework_after_done | 0 |
| harness_hardened | yes — расчёт контраста `frontend/src/test/contrast.ts`; тест темы с таблицей ролей Mantine целиком; оракул сборки: фон и цвет текста схем (F12), наборы знаков шрифта (F6) |
| implement_retries | 4 — по журналу сессии: `?raw` у Vitest пустой (срез 1); типы кортежей в `cssVariablesResolver` — дважды; предупреждение линтера о длине блока тестов |
| verify_fails_before_green | 2 — ранняя приёмка проверяющего: на `5cd724a` две находки по цветам библиотеки и 5 выживших порч из 31; на `1e8e676` 2 выживших из 41 (P7, P8); закрыто до сдачи |
| est_token_or_cost | n/a — работа по подписке, расход сессии не измерялся |

MANUAL-поля заполняет агент/человек на handoff. Если `verify_fails_before_green >= 2` при `harness_hardened: no` — по §9.2 добавь oracle/breaker/hook в этой же поставке.

**Убрано (CONSTITUTION, п. 10):** клоны сверки, порч и опытов исполнителя — удалены после
прогонов; временные профили Chrome и сервер `vite preview` — сняты; черновые файлы исполнителя во
временной папке — выводы прогонов, копии lock, патчи и скрипты среза: их итоги — в этом отчёте.
Снимки — в `docs/deliveries/ch1-04b-shots/`, вне git. После слияния — рабочая копия
`.claude/worktrees/ch1-04b` и ветка `ch1-04b-front-theme`.
