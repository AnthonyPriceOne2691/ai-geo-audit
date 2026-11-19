// Тема «Фарфор и лазурь». Значения токенов тест читает из tokens.css с диска тем же разбором, что в
// tokens.ts. Каскад CSS jsdom не считает: про тему проверяется то, что она пишет в элемент и в
// переменные Mantine.
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  Card,
  Container,
  DEFAULT_THEME,
  Table,
  Text,
  Transition,
  defaultCssVariablesResolver,
  mergeMantineTheme,
} from '@mantine/core';
import { screen } from '@testing-library/react';
import type { CSSProperties, ReactNode } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { contrast, inGamut } from '../test/contrast';
import { renderWithProviders } from '../test/render';

import { cssVariablesResolver, theme } from './theme';
import { SCHEMES, cssVar, parseBlocks, schemeColors } from './tokens';

const TOKENS = join(dirname(fileURLToPath(import.meta.url)), 'tokens.css');
const blocks = parseBlocks(readFileSync(TOKENS, 'utf8'));
const root = blocks.get(':root') ?? {};
const page = blocks.get(':root body') ?? {};
const colors = { light: schemeColors(blocks, 'light'), dark: schemeColors(blocks, 'dark') };
const merged = mergeMantineTheme(DEFAULT_THEME, theme);

// Что, на чём, норма — 29 пар расчёта вида на схему: текст на фонах, подпись главной кнопки,
// ссылка, статусы на подложках; кромки и кольцо фокуса.
const PAIRS: readonly (readonly [string, string, number])[] = [
  ['ink', 'bg', 4.5],
  ['ink', 'surface', 4.5],
  ['ink', 'tint', 4.5],
  ['soft', 'bg', 4.5],
  ['soft', 'surface', 4.5],
  ['soft', 'sunken', 4.5],
  ['faint', 'bg', 4.5],
  ['faint', 'surface', 4.5],
  ['faint', 'sunken', 4.5],
  ['faint', 'tint', 4.5],
  ['onPrimary', 'primary', 4.5],
  ['accent', 'bg', 4.5],
  ['accent', 'surface', 4.5],
  ['accent', 'tint', 4.5],
  ['ok', 'surface', 4.5],
  ['ok', 'okTint', 4.5],
  ['ok', 'tint', 4.5],
  ['warn', 'surface', 4.5],
  ['warn', 'warnTint', 4.5],
  ['warn', 'tint', 4.5],
  ['crit', 'surface', 4.5],
  ['crit', 'critTint', 4.5],
  ['crit', 'tint', 4.5],
  ['ring', 'bg', 3],
  ['ring', 'surface', 3],
  ['edge', 'surface', 3],
  ['edge', 'bg', 3],
  ['primaryEdge', 'surface', 3],
  ['primaryEdge', 'bg', 3],
];

const comma = (text: string): string => text.replace('.', ',');
// prettier пишет числа CSS по-своему (`.2` → `0.2`): значения сравниваются числом.
const byValue = (text: string): string => text.replace(/\d*\.?\d+/g, (n) => String(Number(n)));

// Переменные Mantine, которые тема оставляет библиотеке, — поимённо и с причиной.
const LIBRARY_LEFT = new Map([
  ['--mantine-color-scheme', 'не цвет: имя схемы для color-scheme'],
  ['--mantine-color-disabled', 'вид выключенного — Ч1.4г, кнопки'],
  ['--mantine-color-disabled-color', 'вид выключенного — Ч1.4г, кнопки'],
  ['--mantine-color-disabled-border', 'вид выключенного — Ч1.4г, кнопки'],
]);

/** Переменная выше по дереву — так её ставит расширение браузера. */
function underLeak(children: ReactNode) {
  return <div style={{ '--text-color': '#000' } as CSSProperties}>{children}</div>;
}

const textColorVar = (text: string): string =>
  screen.getByText(text).style.getPropertyValue('--text-color');

describe('щит цвета текста', () => {
  it('F10: текст без пропса цвета держит цвет темы — чужая --text-color до него не доходит', () => {
    renderWithProviders(underLeak(<Text>Проверено страниц</Text>));
    // `currentColor` в `color` — «как у родителя», а цвет страницы задают токены.
    expect(textColorVar('Проверено страниц')).toBe('currentColor');
  });

  it('F10: с пропсом цвета — цвет пропса, щит его не перекрывает', () => {
    renderWithProviders(underLeak(<Text color={cssVar('crit')}>Отказ</Text>));
    expect(textColorVar('Отказ')).toBe(cssVar('crit'));
  });

  it('F10: приглушённый текст остаётся приглушённым', () => {
    renderWithProviders(underLeak(<Text c="dimmed">Подпись колонки</Text>));
    expect(screen.getByText('Подпись колонки').style.color).toBe('var(--mantine-color-dimmed)');
  });
});

describe('контраст токенов', () => {
  it('F11: в обеих схемах одни 20 цветов, и 29 пар расчёта называют их', () => {
    expect(Object.keys(colors.light)).toHaveLength(20);
    expect(Object.keys(colors.dark).sort()).toEqual(Object.keys(colors.light).sort());
    expect(PAIRS).toHaveLength(29);
    const unknown = PAIRS.flatMap(([fg, bg]) => [fg, bg]).filter((role) => !(role in colors.light));
    expect(unknown).toEqual([]);
  });

  it.each(SCHEMES)('F11: схема %s — текст ≥ 4,5 : 1, кромки и кольцо фокуса ≥ 3 : 1', (scheme) => {
    const low = PAIRS.flatMap(([fg, bg, need]) => {
      const ratio = contrast(colors[scheme][fg] ?? '', colors[scheme][bg] ?? '');
      const line = `${fg} на ${bg}: ${comma(ratio.toFixed(2))} при норме ${comma(String(need))}`;
      return ratio < need ? [line] : [];
    });
    expect(low).toEqual([]);
  });

  it.each(SCHEMES)('F11: схема %s — все цвета в охвате sRGB', (scheme) => {
    const outside = Object.entries(colors[scheme]).filter(([, value]) => !inGamut(value));
    expect(outside.map(([role, value]) => `${role}: ${value}`)).toEqual([]);
  });

  it('F11: тема красит Mantine теми же цветами файла токенов, что идут в расчёт', () => {
    const resolved = cssVariablesResolver(merged);
    for (const scheme of SCHEMES) {
      const own = new Set(Object.keys(colors[scheme]).map(cssVar));
      const stray = Object.entries(resolved[scheme]).filter(([, value]) => !own.has(value));
      expect(stray, `схема ${scheme}: переменные Mantine не на токенах`).toEqual([]);
      expect(resolved[scheme]['--mantine-color-text']).toBe(cssVar('ink'));
      expect(resolved[scheme]['--mantine-color-dimmed']).toBe(cssVar('faint'));
      expect(resolved[scheme]['--mantine-color-anchor']).toBe(cssVar('accent'));
    }
  });

  it('F11: каждая цветовая роль Mantine вне палитр — на токене, кроме названных поимённо', () => {
    // Роль без строки в теме берёт цвет библиотеки молча — и после обновления Mantine тоже.
    const library = defaultCssVariablesResolver(merged);
    const palettes = Object.keys(merged.colors).map((color) => `--mantine-color-${color}-`);
    const primary = Object.keys(library.variables).filter((name) =>
      /^--mantine-primary-color-[a-z]/.test(name),
    );
    const roles = [...Object.keys(library.light), ...primary].filter(
      (name) => !palettes.some((prefix) => name.startsWith(prefix)) && !LIBRARY_LEFT.has(name),
    );
    expect(roles).toContain('--mantine-primary-color-filled-hover');
    const resolved = cssVariablesResolver(merged);
    for (const scheme of SCHEMES) {
      const bare = roles.filter((name) => !(name in resolved[scheme]));
      expect(bare, `схема ${scheme}: роли Mantine без токена`).toEqual([]);
    }
  });
});

/** matchMedia системы, которая просит меньше движения. */
function reducedMotion(query: string): MediaQueryList {
  return {
    matches: query === '(prefers-reduced-motion: reduce)',
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  };
}

/** Появление Mantine: стили, которые Transition отдаёт при показе. */
function Appear({ mounted, seen }: { mounted: boolean; seen: CSSProperties[] }) {
  return (
    <Transition mounted={mounted} transition="fade" duration={260}>
      {(styles) => {
        seen.push(styles);
        return <div>Появление</div>;
      }}
    </Transition>
  );
}

describe('меньше движения', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('F14: при «меньше движения» появление Mantine — без перехода, длительность 0', async () => {
    vi.stubGlobal('matchMedia', reducedMotion);
    const seen: CSSProperties[] = [];
    const { rerender } = renderWithProviders(<Appear mounted={false} seen={seen} />);
    rerender(<Appear mounted seen={seen} />);
    await screen.findByText('Появление');
    // Длительность 0 Mantine отдаёт пустыми стилями: перехода нет вовсе.
    expect(seen.at(-1)).toEqual({});
  });

  it('F14: правило «меньше движения» в токенах обнуляет каждую длительность', () => {
    const durations = Object.keys(root).filter((name) => /^\d+m?s$/.test(root[name] ?? ''));
    expect(durations.length).toBeGreaterThan(0);
    const reduced = blocks.get('@media (prefers-reduced-motion: reduce) :root') ?? {};
    const after = Object.fromEntries(durations.map((name) => [name, reduced[name]]));
    expect(after).toEqual(Object.fromEntries(durations.map((name) => [name, '0ms'])));
  });
});

describe('числа темы', () => {
  it('F15: движение токенами — 120, 180 и 260 мс и одна кривая cubic-bezier(.2, .8, .2, 1)', () => {
    const values = Object.values(root);
    const durations = values.filter((value) => /^\d+m?s$/.test(value));
    expect(durations.sort()).toEqual(['120ms', '180ms', '260ms']);
    const curves = values.filter((value) => value.startsWith('cubic-bezier('));
    expect(curves.map(byValue)).toEqual([byValue('cubic-bezier(.2, .8, .2, 1)')]);
  });

  it('F15: скругления — карточка 16 px, кнопка, поле и метка 10 px; тема — токенами', () => {
    expect([root['--audit-radius-card'], root['--audit-radius-control']]).toEqual(['16px', '10px']);
    expect(merged.radius[String(merged.defaultRadius)]).toBe(cssVar('radiusControl'));
    renderWithProviders(<Card>Карточка</Card>);
    const card = screen.getByText('Карточка').style.getPropertyValue('--paper-radius');
    const step = /^var\(--mantine-radius-(\w+)\)$/.exec(card)?.[1] ?? card;
    expect(merged.radius[step]).toBe(cssVar('radiusCard'));
    // Других скруглений нет: каждая ступень Mantine — один из двух токенов.
    const steps = new Set(Object.values(merged.radius));
    expect(steps).toEqual(new Set([cssVar('radiusCard'), cssVar('radiusControl')]));
  });

  it('F15: шрифт Geologica у текста и заголовков, числа табличные — токенами', () => {
    expect(root['--audit-font']).toMatch(/^'Geologica Variable', /);
    expect([merged.fontFamily, merged.headings.fontFamily]).toEqual([
      cssVar('font'),
      cssVar('font'),
    ]);
    expect(page['font-family']).toBe(cssVar('font'));
    expect(page['font-variant-numeric']).toBe('tabular-nums');
  });

  it('F15: рабочая область не шире 1360 px, поля по 28 px — у Container темы', () => {
    expect([root['--audit-workspace'], root['--audit-gutter']]).toEqual(['1360px', '28px']);
    renderWithProviders(<Container>Рабочая область</Container>);
    const area = screen.getByText('Рабочая область');
    expect(area.style.getPropertyValue('--container-size')).toBe(cssVar('workspace'));
    expect(area.style.paddingInline).toBe(cssVar('gutter'));
  });
});

describe('таблица', () => {
  it('F18: таблица Mantine без пропсов выравнивания — заголовок и ячейки по центру из темы', () => {
    renderWithProviders(
      <Table>
        <Table.Thead>
          <Table.Tr>
            <Table.Th>Страница</Table.Th>
            <Table.Th>Оценка</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          <Table.Tr>
            <Table.Td>Главная</Table.Td>
            <Table.Td>82</Table.Td>
          </Table.Tr>
        </Table.Tbody>
      </Table>,
    );
    const cells = [...screen.getAllByRole('columnheader'), ...screen.getAllByRole('cell')];
    expect(cells.map((cell) => cell.style.textAlign)).toEqual(Array(4).fill('center'));
  });
});

// F15: таблицы цветов и чисел в frontend/UI_RULES.md. Колонка значения → схема; у чисел схемы нет.
const RULES = join(dirname(TOKENS), '..', '..', 'UI_RULES.md');
const VALUE_COLUMNS = new Map([
  ['Светлая', 'light'],
  ['Тёмная', 'dark'],
  ['Значение', ''],
]);

/** Значения токенов в таблицах правил: «имя схема» у цвета, «имя» у числа → значения клеток. */
function ruleCells(markdown: string): Map<string, string[]> {
  const cells = new Map<string, string[]>();
  let head: string[] = [];
  for (const line of markdown.split('\n')) {
    const row = line.startsWith('|') ? line.split('|').slice(1, -1) : [];
    const [name = '', ...rest] = row.map((cell) => cell.trim().replace(/^`(.*)`$/, '$1'));
    if (row.length === 0 || name === 'Токен') head = rest;
    else if (name.startsWith('--audit-')) {
      rest.forEach((value, at) => {
        const scheme = VALUE_COLUMNS.get(head[at] ?? '');
        const key = scheme ? `${name} ${scheme}` : name;
        if (scheme !== undefined) cells.set(key, [...(cells.get(key) ?? []), value]);
      });
    }
  }
  return cells;
}

/** Токены tokens.css в тех же ключах: цвета схем и числа корня. */
function tokenCells(): Map<string, string> {
  const cells = new Map(Object.entries(root).filter(([name]) => name.startsWith('--audit-')));
  for (const scheme of SCHEMES) {
    const block = blocks.get(`:root[data-mantine-color-scheme='${scheme}']`) ?? {};
    for (const [name, value] of Object.entries(block)) cells.set(`${name} ${scheme}`, value);
  }
  return cells;
}

/** Расхождения правил с токенами — токен и оба значения; строка дважды — тоже расхождение. */
function drift(rules: Map<string, string[]>, tokens: Map<string, string>): string[] {
  return [...new Set([...tokens.keys(), ...rules.keys()])].flatMap((key) => {
    const found = rules.get(key) ?? [];
    const [rule, token] = [found.join(' | '), tokens.get(key)];
    if (token === undefined) return [`${key}: в правилах ${rule}, в токенах нет`];
    if (found.length === 0) return [`${key}: в токенах ${token}, в правилах строки нет`];
    const same = found.length === 1 && byValue(rule) === byValue(token);
    return same ? [] : [`${key}: в правилах ${rule}, в токенах ${token}`];
  });
}

describe('правила интерфейса', () => {
  it('F15: таблицы цветов и чисел frontend/UI_RULES.md — ровно токены, в обе стороны', () => {
    const rules = ruleCells(readFileSync(RULES, 'utf8'));
    expect(drift(rules, tokenCells())).toEqual([]);
  });

  it('F15: в таблице чисел — 120, 180 и 260 мс и общая кривая cubic-bezier(.2, .8, .2, 1)', () => {
    const rules = ruleCells(readFileSync(RULES, 'utf8'));
    const numbers = [...rules].filter(([key]) => !key.includes(' ')).flatMap(([, cell]) => cell);
    expect(numbers.map(byValue)).toEqual(
      expect.arrayContaining(['120ms', '180ms', '260ms', byValue('cubic-bezier(.2, .8, .2, 1)')]),
    );
  });
});
