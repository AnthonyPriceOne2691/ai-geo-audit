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
const CHAIN = ['lint', 'format:check', 'typecheck', 'test', 'build', 'check:dist'];

interface PackageJson {
  engines?: { node?: string };
  scripts?: Record<string, string>;
  dependencies?: Record<string, string>;
  devDependencies?: Record<string, string>;
}

interface LockEntry {
  resolved?: string;
  integrity?: string;
  hasInstallScript?: boolean;
}

interface Approved {
  dependencies: Record<string, string>;
  devDependencies: Record<string, string>;
  installScripts: Record<string, string>;
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

  it('F3: npm run check — шесть шагов по порядку, каждый шаг есть в scripts', () => {
    const scripts = readJson<PackageJson>('package.json').scripts ?? {};
    const steps = (scripts.check ?? '')
      .split('&&')
      .map((part) => part.trim().replace(/^npm (run )?/, ''));
    expect(steps).toEqual(CHAIN);
    const missing = CHAIN.filter((step) => !scripts[step]);
    expect(missing, 'шаги цепочки без своего скрипта').toEqual([]);
    expect(scripts.typecheck).toBe('tsc --noEmit');
    expect(scripts.build).toBe('vite build');
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
    ];
    const ignored = execFileSync('git', ['check-ignore', '--no-index', ...paths], {
      cwd: ROOT,
      env,
      encoding: 'utf8',
    });
    expect(ignored.trim().split('\n')).toEqual(paths);
  });

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

  it('F17: скрипты установки выключены, пакеты со скриптом названы поимённо с причиной', () => {
    expect(npmrc().get('ignore-scripts')).toBe('true');
    expect(npmrc().get('registry')).toBe(REGISTRY);
    const withScripts = lockEntries()
      .filter(([, e]) => e.hasInstallScript)
      .map(([path]) => path.replace(/^.*node_modules\//, ''));
    const approved = readJson<Approved>('approved-packages.json');
    expect([...new Set(withScripts)].sort()).toEqual(Object.keys(approved.installScripts).sort());
  });
});
