// Схема (F12, F13, F19, F20). Скрипт схемы из `public/` тест читает с диска и исполняет в окне
// jsdom — тот же текст, что ляжет в сборку. Менеджер Mantine, переключатель и переход документа —
// через заглушку с общими провайдерами. Вид смены jsdom не показывает: здесь — путь вызова, вид —
// просмотр проверяющей.
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { act, cleanup, fireEvent, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { StubPage } from '../StubPage';
import { renderWithProviders } from '../test/render';
import { words } from '../words';

import { SCHEME_KEY } from './scheme';
import { parseBlocks } from './tokens';

const HERE = dirname(fileURLToPath(import.meta.url));
const SCRIPT = join(HERE, '..', '..', 'public', 'color-scheme.js');
const TOKENS = join(HERE, 'tokens.css');
const ATTRIBUTE = 'data-mantine-color-scheme';
const DARK_QUERY = '(prefers-color-scheme: dark)';
const REDUCE_QUERY = '(prefers-reduced-motion: reduce)';

type Listener = (event: MediaQueryListEvent) => void;

/** Система: тёмная или светлая, «меньше движения» или нет; смена схемы — событием `change`. */
function system(dark: boolean, reduce = false) {
  const state = { dark, reduce };
  const listeners = new Map<string, Set<Listener>>();
  const of = (query: string) => listeners.get(query) ?? listeners.set(query, new Set()).get(query);
  const media = (query: string) => ({
    get matches() {
      if (query === DARK_QUERY) return state.dark;
      return query === REDUCE_QUERY ? state.reduce : false;
    },
    media: query,
    onchange: null,
    addEventListener: (_type: string, listener: Listener) => of(query)?.add(listener),
    removeEventListener: (_type: string, listener: Listener) => of(query)?.delete(listener),
    addListener: (listener: Listener) => of(query)?.add(listener),
    removeListener: (listener: Listener) => of(query)?.delete(listener),
    dispatchEvent: () => false,
  });
  vi.stubGlobal('matchMedia', media);
  return {
    turnDark(next: boolean) {
      state.dark = next;
      const event = { matches: next, media: DARK_QUERY } as MediaQueryListEvent;
      for (const listener of of(DARK_QUERY) ?? []) listener(event);
    },
    reduceMotion(next: boolean) {
      state.reduce = next;
    },
  };
}

// Хранилище окна подменяется своим свойством поверх того, что дал jsdom, и возвращается после
// теста: обращение к `window.localStorage` бросает в Chrome при запрете хранилища само по себе.
const OWN_STORAGE = Object.getOwnPropertyDescriptor(window, 'localStorage');

function storageThrows(where: 'access' | 'read') {
  const denied = () => new DOMException('хранилище запрещено', 'SecurityError');
  const get =
    where === 'access'
      ? () => {
          throw denied();
        }
      : () => ({
          getItem: () => {
            throw denied();
          },
        });
  Object.defineProperty(window, 'localStorage', { configurable: true, get });
}

function restoreStorage() {
  if (OWN_STORAGE) Object.defineProperty(window, 'localStorage', OWN_STORAGE);
  else Reflect.deleteProperty(window, 'localStorage');
}

/** Скрипт схемы, как в браузере до приложения: атрибут у корня после него. */
function runScript(): string | null {
  document.documentElement.removeAttribute(ATTRIBUTE);
  // Текст файла исполняется как есть — так его исполнит браузер из тега в <head>.
  new Function(readFileSync(SCRIPT, 'utf8'))();
  return document.documentElement.getAttribute(ATTRIBUTE);
}

const scheme = () => document.documentElement.getAttribute(ATTRIBUTE);

/** Переход документа, как в Chrome: смена — внутри обратного вызова; схема до и после него. */
function viewTransition() {
  const seen: (string | null)[][] = [];
  const start = vi.fn((update: () => void) => {
    const before = scheme();
    update();
    seen.push([before, scheme()]);
    const done = Promise.resolve();
    return {
      finished: done,
      ready: done,
      updateCallbackDone: done,
      skipTransition: () => undefined,
    };
  });
  Object.defineProperty(document, 'startViewTransition', { configurable: true, value: start });
  return { start, seen };
}

function choose(label: string) {
  fireEvent.click(screen.getByRole('radio', { name: label }));
}

afterEach(() => {
  restoreStorage();
  vi.unstubAllGlobals();
  Reflect.deleteProperty(document, 'startViewTransition');
  window.localStorage.removeItem(SCHEME_KEY);
  document.documentElement.removeAttribute(ATTRIBUTE);
});

describe('скрипт схемы', () => {
  it.each([
    ['«светлая» при тёмной системе', 'light', true, 'light'],
    ['«тёмная» при светлой системе', 'dark', false, 'dark'],
    ['«как в системе» при тёмной системе', 'auto', true, 'dark'],
    ['«как в системе» при светлой системе', 'auto', false, 'light'],
    ['пустое значение при тёмной системе', '', true, 'dark'],
    ['ключа нет при тёмной системе', null, true, 'dark'],
    ['чужое значение при тёмной системе', 'blue', true, 'dark'],
  ] as const)('F12: %s — схема %s', (_case, stored, dark, want) => {
    system(dark);
    if (stored !== null) window.localStorage.setItem(SCHEME_KEY, stored);
    expect(runScript()).toBe(want);
  });

  it('F12: бросает само обращение к window.localStorage — по системе, без ошибки', () => {
    system(true);
    storageThrows('access');
    expect(runScript()).toBe('dark');
  });

  it('F12: бросает чтение ключа — по системе, без ошибки', () => {
    system(true);
    storageThrows('read');
    expect(runScript()).toBe('dark');
  });

  it('F12: скрипт и менеджер Mantine — один ключ: выбор переключателем скрипт ставит после перезагрузки', () => {
    system(false);
    renderWithProviders(<StubPage />);
    choose(words.scheme.dark);
    expect(window.localStorage.getItem(SCHEME_KEY)).toBe('dark');
    cleanup();
    expect(runScript()).toBe('dark');
  });
});

describe('менеджер схемы', () => {
  it('F13: «как в системе» — система стала тёмной, страница тёмная без перезагрузки', () => {
    const os = system(false);
    renderWithProviders(<StubPage />);
    expect(scheme()).toBe('light');
    act(() => {
      os.turnDark(true);
    });
    expect(scheme()).toBe('dark');
  });

  it('F13: выбор в другой вкладке — схема меняется по событию хранилища', () => {
    system(false);
    renderWithProviders(<StubPage />);
    act(() => {
      window.localStorage.setItem(SCHEME_KEY, 'dark');
      const event = { key: SCHEME_KEY, newValue: 'dark', storageArea: window.localStorage };
      window.dispatchEvent(new StorageEvent('storage', event));
    });
    expect(scheme()).toBe('dark');
  });
});

describe('переключатель схемы', () => {
  it('F20: на заглушке — «Светлая», «Тёмная» и «Как в системе»; выбрано «Как в системе»', () => {
    system(false);
    renderWithProviders(<StubPage />);
    const group = screen.getByRole('radiogroup', { name: words.scheme.label });
    const labels = [...group.querySelectorAll('label')].map((label) => label.textContent);
    expect(labels).toEqual([words.scheme.light, words.scheme.dark, words.scheme.auto]);
    expect(screen.getByRole('radio', { name: words.scheme.auto })).toBeChecked();
  });

  it('F19: повторный выбор того же положения ничего не меняет', () => {
    system(false);
    const { start } = viewTransition();
    renderWithProviders(<StubPage />);
    choose(words.scheme.auto);
    expect(start).not.toHaveBeenCalled();
    expect(window.localStorage.getItem(SCHEME_KEY)).toBeNull();
  });

  it('F19: с переходом документа — смена внутри него, текст и фон вместе', () => {
    system(false);
    const { start, seen } = viewTransition();
    renderWithProviders(<StubPage />);
    choose(words.scheme.dark);
    expect(start).toHaveBeenCalledTimes(1);
    expect(seen).toEqual([['light', 'dark']]);
  });

  it('F19: без перехода документа — мгновенно', () => {
    system(false);
    renderWithProviders(<StubPage />);
    choose(words.scheme.dark);
    expect(scheme()).toBe('dark');
  });

  it('F19: при «меньше движения» — мгновенно, перехода нет', () => {
    system(false, true);
    const { start } = viewTransition();
    renderWithProviders(<StubPage />);
    choose(words.scheme.dark);
    expect(start).not.toHaveBeenCalled();
    expect(scheme()).toBe('dark');
  });

  it('F19: «меньше движения» читается в момент смены, а не при отрисовке', () => {
    const os = system(false, false);
    const { start } = viewTransition();
    renderWithProviders(<StubPage />);
    os.reduceMotion(true);
    choose(words.scheme.dark);
    expect(start).not.toHaveBeenCalled();
    expect(scheme()).toBe('dark');
  });

  it('F19: бегунок и подписи движутся токеном 260 мс и общей кривой, а не 200 мс `ease` библиотеки', () => {
    system(false);
    renderWithProviders(<StubPage />);
    const group = screen.getByRole('radiogroup', { name: words.scheme.label });
    expect(group.style.getPropertyValue('--sc-transition-duration')).toBe(
      'var(--audit-motion-slow)',
    );
    expect(group.style.getPropertyValue('--sc-transition-timing-function')).toBe(
      'var(--audit-motion-ease)',
    );
    const indicator = group.querySelector<HTMLElement>('.mantine-SegmentedControl-indicator');
    expect(indicator, 'бегунка нет').not.toBeNull();
    expect(indicator?.style.transitionTimingFunction).toBe('var(--audit-motion-ease)');
  });

  it('F19: переход документа — длительностью и кривой токенов; при «меньше движения» токен уже 0', () => {
    const blocks = parseBlocks(readFileSync(TOKENS, 'utf8'));
    expect(blocks.get('::view-transition-old(root), ::view-transition-new(root)')).toEqual({
      'animation-duration': 'var(--audit-motion-slow)',
      'animation-timing-function': 'var(--audit-motion-ease)',
    });
    const reduced = blocks.get('@media (prefers-reduced-motion: reduce) :root') ?? {};
    expect(reduced['--audit-motion-slow']).toBe('0ms');
  });
});
