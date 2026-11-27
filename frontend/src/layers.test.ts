// @vitest-environment node
// A384: гейт слоёв TS — dependency-cruiser с конфигом корня на копиях `frontend/src`, как у гейта:
// дерево ветки — без нарушений, модулей больше нуля; каждая копия — ненулевой код и ровно свои
// правила (находка 1 второй проверяющей: цикл прятал правило темы, помощников тестов не держало
// ничто, `severity: 'warn'` проходил молча).
import { spawnSync } from 'node:child_process';
import { appendFileSync, cpSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

const FRONT = join(dirname(fileURLToPath(import.meta.url)), '..');

/** Прогон из корня копии: `frontend/src`, конфиг корня, вывод `err` — как в check_layers_gate.sh. */
function cruise(
  change: (src: string) => void,
  output = 'err',
): { code: number | null; out: string } {
  const tree = mkdtempSync(join(tmpdir(), 'layers-'));
  try {
    cpSync(join(FRONT, 'src'), join(tree, 'frontend', 'src'), { recursive: true });
    cpSync(join(FRONT, 'tsconfig.json'), join(tree, 'frontend', 'tsconfig.json'));
    cpSync(join(FRONT, '..', '.dependency-cruiser.cjs'), join(tree, '.dependency-cruiser.cjs'));
    change(join(tree, 'frontend', 'src'));
    const run = spawnSync(
      join(FRONT, 'node_modules', '.bin', 'depcruise'),
      ['frontend/src', '--config', '.dependency-cruiser.cjs', '--output-type', output],
      { cwd: tree, encoding: 'utf8' },
    );
    return { code: run.status, out: `${run.stdout}${run.stderr}` };
  } finally {
    rmSync(tree, { recursive: true, force: true });
  }
}

/** Имена правил, названных в выводе, — без повторов и по алфавиту. */
const named = (out: string): string[] =>
  [
    ...new Set([...out.matchAll(/^\s*(?:error|warn|info) ([a-z-]+):/gm)].map((m) => m[1] ?? '')),
  ].sort();

const append = (src: string, file: string, line: string): void =>
  appendFileSync(join(src, file), `${line}\n`);

const COPIES: [string, (src: string) => void, string[]][] = [
  [
    'тема берёт StubPage — копия спеки, она же цикл',
    (src) => append(src, 'theme/tokens.ts', "import '../StubPage';"),
    ['no-circular', 'theme-below-pages'],
  ],
  [
    'тема берёт новый модуль, цикла нет',
    (src) => {
      writeFileSync(join(src, 'words2.ts'), 'export const extra = 1;\n');
      append(src, 'theme/theme.ts', "import '../words2';");
    },
    ['theme-below-pages'],
  ],
  [
    'цикл двух модулей вне темы',
    (src) => append(src, 'words.ts', "import './StubPage';"),
    ['no-circular'],
  ],
  [
    'страница берёт помощника тестов',
    (src) => append(src, 'StubPage.tsx', "import './test/contrast';"),
    ['no-test-helpers-in-app'],
  ],
  [
    // Заглушка берёт блок связи, тот — клиент: копия спеки — ещё и цикл, как у темы выше.
    'клиент API берёт страницу заглушки — копия спеки F52, она же цикл',
    (src) => append(src, 'api/client.ts', "import '../StubPage';"),
    ['api-below-pages', 'no-circular'],
  ],
  [
    'новый модуль слоя API берёт страницу заглушки, цикла нет — F52',
    (src) => writeFileSync(join(src, 'api', 'probe.ts'), "import '../StubPage';\n"),
    ['api-below-pages'],
  ],
  [
    'слой API берёт тему — F52',
    (src) => append(src, 'api/health.ts', "import '../theme/tokens';"),
    ['api-below-pages'],
  ],
];

describe('слои фронта — dependency-cruiser', () => {
  it('A384: дерево ветки — нарушений нет, модулей больше нуля', () => {
    const { code, out } = cruise(() => undefined);
    expect(code, out).toBe(0);
    expect(Number(/(\d+) modules/.exec(out)?.[1] ?? 0)).toBeGreaterThan(0);
  });

  it('F52: модули слоя src/api/ на ветке — больше нуля, правило их видит', () => {
    const { code, out } = cruise(() => undefined, 'json');
    expect(code, out).toBe(0);
    const modules = (JSON.parse(out) as { modules: { source: string }[] }).modules;
    expect(modules.filter((m) => m.source.startsWith('frontend/src/api/')).length).toBeGreaterThan(
      0,
    );
  });

  it.each(COPIES)('A384: %s — красное с именем правила', (_, change, rules) => {
    const { code, out } = cruise(change);
    expect(code, out).not.toBe(0);
    expect(named(out), out).toEqual(rules);
  });
});
