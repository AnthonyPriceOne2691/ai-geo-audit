// @vitest-environment node
// A383, A385, A387, A388: гейты сложности, мёртвого кода, предупреждений eslint и дублей — сами
// скрипты `scripts/lint/` на временной копии корня, окружением хуков и джобы `gates`: дерево ветки —
// код 0 и просмотрено больше нуля файлов; каждая копия с нарушением — ненулевой код и строка гейта
// о своём нарушении (находка 5 второй проверяющей: план обещал тесты-копии на каждую роль, были
// пробы руками).
import { spawnSync } from 'node:child_process';
import { appendFileSync, cpSync, mkdtempSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

const FRONT = join(dirname(fileURLToPath(import.meta.url)), '..');
const ROOT = join(FRONT, '..');
const SKIP = new Set(['node_modules', 'dist', 'coverage', '__pycache__']);
const SLOW = 30_000;

const GATES = {
  complexity: ['bash', 'scripts/lint/check_complexity_gate.sh'],
  'dead-code': ['python3', 'scripts/lint/check_dead_code.py'],
  'eslint-warnings': ['bash', 'scripts/lint/check_eslint_warnings.sh'],
  jscpd: ['bash', 'scripts/lint/check_jscpd_gate.sh'],
} as const;
type Gate = keyof typeof GATES;
type Change = (src: string) => void;
type Row = [string, Change, string | null];

/** Окружение хуков и `gates`; свои LINT_*, STRICT и GIT_* разработчика в прогон не протекают. */
function gateEnv(): NodeJS.ProcessEnv {
  const own = Object.entries(process.env).filter(([key]) => !/^(LINT_|GIT_|STRICT$)/.test(key));
  return {
    ...Object.fromEntries(own),
    STRICT: '1',
    LINT_FE_DIR: 'frontend',
    LINT_BE_DIR: 'backend',
    LINT_PY_SRC: 'backend/src',
  };
}

/** Git копии: гейт мёртвого кода судит только отслеживаемые файлы. */
function git(dir: string, ...args: string[]): void {
  const run = spawnSync('git', args, { cwd: dir, env: gateEnv(), encoding: 'utf8' });
  if (run.status !== 0) throw new Error(`git ${args.join(' ')}: ${run.stderr}`);
}

/** Копия корня: `scripts/lint` и фронт без сборки (пакеты — ссылкой), правка, свой git. */
function build(dir: string, change: Change): void {
  const filter = (path: string): boolean => !SKIP.has(basename(path));
  cpSync(join(ROOT, 'scripts', 'lint'), join(dir, 'scripts', 'lint'), { recursive: true, filter });
  cpSync(FRONT, join(dir, 'frontend'), { recursive: true, filter });
  symlinkSync(join(FRONT, 'node_modules'), join(dir, 'frontend', 'node_modules'), 'dir');
  writeFileSync(join(dir, '.gitignore'), 'node_modules\n__pycache__\n');
  change(join(dir, 'frontend', 'src'));
  git(dir, 'init', '-q');
  git(dir, 'add', '-A');
}

/** Итог гейта на копии одной строкой: «OK» — код 0 и просмотрено больше нуля файлов; красное —
 * ненулевой код и строка гейта о нарушении; иначе — код и вывод целиком, чтобы падение читалось. */
function verdict(gate: Gate, change: Change, line: string | null): string {
  const dir = mkdtempSync(join(tmpdir(), 'gates-'));
  try {
    build(dir, change);
    const [exe, script] = GATES[gate];
    const run = spawnSync(exe, [script], { cwd: dir, env: gateEnv(), encoding: 'utf8' });
    const out = `${run.stdout}${run.stderr}`;
    const seen = Number(/просмотрено (\d+) файл/.exec(out)?.[1] ?? 0);
    if (run.status === 0 && seen > 0) return 'OK';
    if (run.status !== 0 && line !== null && out.includes(line)) return `красное: ${line}`;
    return `код ${run.status}, просмотрено ${seen}:\n${out}`;
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
}

const want = (line: string | null): string => (line === null ? 'OK' : `красное: ${line}`);

const NONE: Change = () => undefined;

const append =
  (file: string, code: string): Change =>
  (src) =>
    appendFileSync(join(src, file), `\n${code}\n`);

/** Функция сложности 11 — на одну ветку выше порога гейта (10). */
const branchy = [
  'export function probeBranches(n: number): number {',
  ...Array.from({ length: 10 }, (_, i) => `  if (n === ${i}) return ${i};`),
  '  return -1;',
  '}',
].join('\n');

/** Блок длиннее порога jscpd (50 токенов) — одинаковый под разными именами. */
const block = (name: string): string => `export function ${name}(rows: number[]): number {
  let total = 0;
  for (const row of rows) {
    if (row > 10) {
      total += row * 2;
    } else if (row > 5) {
      total += row;
    } else {
      total -= row;
    }
  }
  return total;
}
`;

const twice: Change = (src) => {
  writeFileSync(join(src, 'probe-a.ts'), block('probeA'));
  writeFileSync(join(src, 'probe-b.ts'), block('probeB'));
};

const COMPLEXITY: Row[] = [
  ['дерево ветки', NONE, null],
  [
    'функция сложности 11',
    append('words.ts', branchy),
    'frontend/src/words.ts: 1 нарушений сложности (разрешено 0)',
  ],
];

const DEAD_CODE: Row[] = [
  ['дерево ветки', NONE, null],
  [
    'экспорт, который никто не берёт',
    append('words.ts', 'export const probeUnused = 1;'),
    'frontend/src/words.ts::probeUnused',
  ],
];

const ESLINT: Row[] = [
  ['дерево ветки', NONE, null],
  [
    'новое предупреждение',
    append('words.ts', "export function probeLog(): void {\n  console.log('x');\n}"),
    'warnings выросли: 1 (baseline 0)',
  ],
  [
    'ошибка в файле вне снимка',
    append('words.ts', 'export function probeError(): void {\n  const unused = 1;\n}'),
    'src/words.ts: ошибок 1, разрешено 0',
  ],
];

const JSCPD: Row[] = [
  ['дерево ветки', NONE, null],
  ['блок повторён в двух файлах', twice, 'дубли выросли: 1 clone-пар (baseline 0)'],
];

describe('гейты фронта на копиях — дерево ветки зелёное, копия с нарушением красная', () => {
  it.each(COMPLEXITY)(
    'A383: %s — гейт сложности',
    (_, change, line) => {
      expect(verdict('complexity', change, line)).toBe(want(line));
    },
    SLOW,
  );

  it.each(DEAD_CODE)(
    'A385: %s — гейт мёртвого кода',
    (_, change, line) => {
      expect(verdict('dead-code', change, line)).toBe(want(line));
    },
    SLOW,
  );

  it.each(ESLINT)(
    'A387: %s — ратчет предупреждений и ошибок eslint',
    (_, change, line) => {
      expect(verdict('eslint-warnings', change, line)).toBe(want(line));
    },
    SLOW,
  );

  it.each(JSCPD)(
    'A388: %s — гейт дублей',
    (_, change, line) => {
      expect(verdict('jscpd', change, line)).toBe(want(line));
    },
    SLOW,
  );
});
