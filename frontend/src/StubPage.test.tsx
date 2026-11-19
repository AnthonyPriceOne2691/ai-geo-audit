// Заглушка: имя сервиса, общие провайдеры, строка-факт.
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { cleanup, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { App } from './App';
import { SERVICE_NAME } from './brand';
import { StubPage } from './StubPage';
import { renderWithProviders } from './test/render';

const FRONT = join(dirname(fileURLToPath(import.meta.url)), '..');

/** Что тема и схема оставили в документе: переменные Mantine и атрибут схемы у корня. */
function themeTrace(): { variables: string; scheme: string | null } {
  const styles = [...document.querySelectorAll('style[data-mantine-styles]')];
  return {
    variables: styles.map((style) => style.textContent).join('\n'),
    scheme: document.documentElement.getAttribute('data-mantine-color-scheme'),
  };
}

describe('заглушка', () => {
  it('F8: имя из brand.ts — в заголовке заглушки и в <title> index.html', () => {
    renderWithProviders(<StubPage />);
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe(SERVICE_NAME);
    const html = readFileSync(join(FRONT, 'index.html'), 'utf8');
    expect(html).toContain(`<title>${SERVICE_NAME}</title>`);
  });

  it('F9: тестовый рендер и App — одни провайдеры: тема и схема в документе совпадают', () => {
    render(<App />);
    const app = themeTrace();
    cleanup();
    document.documentElement.removeAttribute('data-mantine-color-scheme');
    renderWithProviders(<StubPage />);
    const test = themeTrace();
    expect(app.variables).not.toBe('');
    expect(app.scheme).not.toBeNull();
    expect(test).toEqual(app);
  });

  it('F16: заглушка называет сервис и то, что на ней есть, без обещаний', () => {
    renderWithProviders(<StubPage />);
    expect(screen.getByText('Сейчас здесь только проверка сборки')).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/скоро/i);
  });
});
