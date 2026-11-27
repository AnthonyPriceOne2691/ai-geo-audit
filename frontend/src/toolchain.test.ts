// @vitest-environment node
// Инструменты фронта: версия Node, предел воркеров, цепочка проверок, игнор следов, пакеты.
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import viteConfig from '../vite.config';

const FRONT = join(dirname(fileURLToPath(import.meta.url)), '..');
const ROOT = join(FRONT, '..');
const REGISTRY = 'https://registry.npmjs.org/';
// Скрипты — договор целиком: флаг мимо конфига (`--maxWorkers`), «|| true», суженная проверка
// формата или новый `pre…`, который под `ignore-scripts` молча не исполнится, — правка этой
// таблицы, а не тихая правка `package.json` (F3).
const SCRIPTS = {
  dev: 'vite',
  preview: 'vite preview',
  lint: 'eslint .',
  format: 'prettier --write .',
  'format:check': 'prettier --check .',
  typecheck: 'tsc --noEmit',
  test: 'vitest run',
  build: 'vite build',
  'check:dist': 'node scripts/check-dist.mjs',
  check:
    'npm run lint && npm run format:check && npm run typecheck && npm test && npm run build' +
    ' && npm run check:dist',
};
// Поля `package.json`: новое поле — правка этого списка. Так не проходят мимо F17 зависимости в
// других полях; `overrides` в списке — только под тестом подстановок ниже: вложенный пакет
// подменяется по одобренному списку, точной версией, без псевдонима.
const PACKAGE_FIELDS = [
  'dependencies',
  'devDependencies',
  'engines',
  'name',
  'overrides',
  'private',
  'scripts',
  'type',
  'version',
];

interface PackageJson {
  engines?: { node?: string };
  scripts?: Record<string, string>;
  dependencies?: Record<string, string>;
  devDependencies?: Record<string, string>;
  overrides?: Record<string, unknown>;
}

interface LockEntry {
  name?: string;
  version?: string;
  resolved?: string;
  integrity?: string;
  hasInstallScript?: boolean;
}

interface Approved {
  dependencies: Record<string, string>;
  devDependencies: Record<string, string>;
  overrides: Record<string, string>;
  installScripts: Record<string, string>;
}

/** Подстановки `overrides` парами «родитель>пакет» — значение; вложенность глубже — не версия. */
function substitutions(overrides: Record<string, unknown>): [string, unknown][] {
  return Object.entries(overrides).flatMap(([parent, inner]): [string, unknown][] =>
    inner !== null && typeof inner === 'object'
      ? Object.entries(inner).map(([name, version]): [string, unknown] => [
          `${parent}>${name}`,
          version,
        ])
      : [[parent, inner]],
  );
}

const readText = (name: string): string => readFileSync(join(FRONT, name), 'utf8');
const readJson = <T>(name: string): T => JSON.parse(readText(name)) as T;

/** Настройки `frontend/.npmrc` без комментариев. */
function npmrc(): Map<string, string> {
  const settings = new Map<string, string>();
  for (const raw of readText('.npmrc').split('\n')) {
    const line = raw.trim();
    const at = line.indexOf('=');
    if (line.startsWith('#') || line.startsWith(';') || at <= 0) continue;
    settings.set(line.slice(0, at).trim(), line.slice(at + 1).trim());
  }
  return settings;
}

/** Записи пакетов в lock: корневая запись — сам проект, у неё нет адреса и integrity. */
function lockEntries(): [string, LockEntry][] {
  const lock = readJson<{ packages: Record<string, LockEntry> }>('package-lock.json');
  return Object.entries(lock.packages).filter(([path]) => path !== '');
}

describe('инструменты фронта', () => {
  it('F1: Node тестов, .nvmrc, engines и engine-strict называют одну версию', () => {
    const wanted = readText('.nvmrc').trim();
    const running = process.versions.node.split('.')[0];
    expect(running, `тесты идут на Node ${process.versions.node}, .nvmrc требует ${wanted}`).toBe(
      wanted,
    );
    const major = Number(wanted);
    expect(readJson<PackageJson>('package.json').engines?.node).toBe(`>=${major} <${major + 1}`);
    expect(npmrc().get('engine-strict')).toBe('true');
  });

  it('F2: у тестов не больше двух воркеров — пределом в конфиге Vite', () => {
    const max = viteConfig.test?.maxWorkers;
    expect(max, 'в vite.config.ts нет test.maxWorkers').toBeDefined();
    expect(Number(max)).toBeGreaterThanOrEqual(1);
    expect(Number(max)).toBeLessThanOrEqual(2);
  });

  it('F3: npm run check — шесть шагов по порядку, скрипты package.json ровно по таблице', () => {
    expect(readJson<PackageJson>('package.json').scripts).toEqual(SCRIPTS);
  });

  it('F4: следы инструментов игнорирует git — node_modules, dist, tsbuildinfo, coverage', () => {
    const env = Object.fromEntries(
      Object.entries(process.env).filter(([key]) => !key.startsWith('GIT_')),
    );
    const paths = [
      'frontend/node_modules/x',
      'frontend/dist/x',
      'frontend/tsconfig.tsbuildinfo',
      'frontend/coverage/x',
      // Песочница и отчёт порч TS: каталоги задаёт `stryker.config.json` в корне (В1б).
      '.stryker-tmp/x',
      'reports/stryker/x',
    ];
    const ignored = execFileSync('git', ['check-ignore', '--no-index', ...paths], {
      cwd: ROOT,
      env,
      encoding: 'utf8',
    });
    expect(ignored.trim().split('\n')).toEqual(paths);
  });
});

// A394: пакеты В1б — точными версиями и по списку одобренных, без скриптов установки, с отсечкой
// возраста в `.npmrc`; копия с нарушением — красный F17. Строки `new_dependency` в STATUS сверяет
// гейт `check_new_dependency.py`.
describe('пакеты фронта', () => {
  it('F17: каждый пакет lock — из реестра npm и с integrity', () => {
    const entries = lockEntries();
    expect(entries.length).toBeGreaterThan(0);
    const foreign = entries.filter(([, e]) => !e.resolved?.startsWith(REGISTRY));
    expect(
      foreign.map(([path]) => path),
      'пакеты не из реестра npm',
    ).toEqual([]);
    const unsigned = entries.filter(([, e]) => !e.integrity?.startsWith('sha512-'));
    expect(
      unsigned.map(([path]) => path),
      'записи lock без integrity',
    ).toEqual([]);
  });

  it('F17: запись lock — тот самый пакет: имя и версия из пути совпадают с адресом архива', () => {
    // Подмена через тот же реестр: под доверенным именем — адрес и integrity чужого архива.
    // Адрес — реестр, integrity сходится с архивом, и проверки выше её не видят.
    const wrong = lockEntries().filter(([path, e]) => {
      const name = path.slice(path.lastIndexOf('node_modules/') + 'node_modules/'.length);
      const file = name.slice(name.lastIndexOf('/') + 1);
      const expected = `${REGISTRY}${name}/-/${file}-${e.version ?? ''}.tgz`;
      return e.name !== undefined || e.version === undefined || e.resolved !== expected;
    });
    expect(
      wrong.map(([path]) => path),
      'записи lock с чужим адресом, без версии или с полем name',
    ).toEqual([]);
  });

  it('F17: поля package.json ровно по списку — зависимостей в других полях нет', () => {
    expect(Object.keys(readJson<Record<string, unknown>>('package.json')).sort()).toEqual(
      PACKAGE_FIELDS,
    );
  });

  it('F17: прямые зависимости — точными версиями и ровно по одобренному списку', () => {
    const pkg = readJson<PackageJson>('package.json');
    const direct = { ...pkg.dependencies, ...pkg.devDependencies };
    const loose = Object.entries(direct).filter(([, version]) => !/^\d+\.\d+\.\d+$/.test(version));
    expect(
      loose.map(([name, version]) => `${name}@${version}`),
      'версии не точные',
    ).toEqual([]);
    const approved = readJson<Approved>('approved-packages.json');
    expect(Object.keys(pkg.dependencies ?? {}).sort()).toEqual(
      Object.keys(approved.dependencies).sort(),
    );
    expect(Object.keys(pkg.devDependencies ?? {}).sort()).toEqual(
      Object.keys(approved.devDependencies).sort(),
    );
  });

  it('F17: overrides — подстановки по одобренному списку, точной версией, без псевдонима', () => {
    const found = substitutions(readJson<PackageJson>('package.json').overrides ?? {});
    const loose = found.filter(([, v]) => typeof v !== 'string' || !/^\d+\.\d+\.\d+$/.test(v));
    expect(
      loose.map(([key]) => key),
      'подстановка не точной версией',
    ).toEqual([]);
    const approved = readJson<Approved>('approved-packages.json');
    expect(found.map(([key, v]) => `${key}@${String(v)}`).sort()).toEqual(
      Object.keys(approved.overrides).sort(),
    );
  });

  it('F17: скрипты установки выключены, пакеты со скриптом названы поимённо с причиной', () => {
    expect(npmrc().get('ignore-scripts')).toBe('true');
    expect(npmrc().get('registry')).toBe(REGISTRY);
    const withScripts = lockEntries()
      .filter(([, e]) => e.hasInstallScript)
      .map(([path]) => path.replace(/^.*node_modules\//, ''));
    const approved = readJson<Approved>('approved-packages.json');
    expect([...new Set(withScripts)].sort()).toEqual(Object.keys(approved.installScripts).sort());
  });

  it('F17: версии моложе 14 суток установка не берёт — настройкой в .npmrc', () => {
    expect(npmrc().get('min-release-age')).toBe('14');
  });
});
