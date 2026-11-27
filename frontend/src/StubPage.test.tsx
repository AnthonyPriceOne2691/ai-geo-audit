// Заглушка: имя сервиса, общие провайдеры, строка-факт, образцы вида.
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { cleanup, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { App } from './App';
import { SERVICE_NAME } from './brand';
import { StubPage } from './StubPage';
import { renderWithProviders } from './test/render';
import { cssVar } from './theme/tokens';
import { words } from './words';

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
    expect(screen.getByText('Сейчас здесь проверка сборки и образцы вида')).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/скоро/i);
  });
});

describe('образцы вида', () => {
  it('F20: на карточке — заголовок, текст, приглушённый текст и ссылка; цвет — из темы', () => {
    renderWithProviders(<StubPage />);
    const { samples } = words;
    const heading = screen.getByRole('heading', { level: 2, name: samples.title });
    const paper = heading.closest<HTMLElement>('.mantine-Paper-root');
    expect(paper, 'заголовок образцов не на карточке').not.toBeNull();
    const card = within(paper as HTMLElement);
    // Цвета в месте вызова нет — у текста цвет темы; приглушённый — роль dimmed темы.
    expect(card.getByText(samples.text).style.color).toBe('');
    expect(card.getByText(samples.muted).style.color).toBe('var(--mantine-color-dimmed)');
    const link = card.getByRole('link', { name: samples.link });
    // Ссылка ведёт на саму таблицу: пустой id дал бы «#» и у ссылки, и у таблицы, и сверка адреса
    // с id прошла бы — порча выжила в гейте порч TS (В1б).
    const target = (link.getAttribute('href') ?? '').replace(/^#/, '');
    expect(target).not.toBe('');
    expect(document.getElementById(target)).toBe(screen.getByRole('table'));
  });

  it('F20: карточка образца — снова Card, без своего фона: фон даёт тема', () => {
    renderWithProviders(<StubPage />);
    const heading = screen.getByRole('heading', { level: 2, name: words.samples.title });
    const card = heading.closest<HTMLElement>('.mantine-Card-root');
    expect(card, 'образцы не на Card').not.toBeNull();
    expect(card?.style.backgroundColor).toBe(cssVar('surface'));
  });

  it('F20: таблица из трёх строк по центру, статусы — словом на подложке токенов', () => {
    renderWithProviders(<StubPage />);
    const table = within(screen.getByRole('table'));
    const statuses = table
      .getAllByRole('row')
      .slice(1)
      .map((row) => {
        const label = within(row).getByText(/^(ОК|Внимание|Ошибка)$/);
        const { color, backgroundColor, borderRadius } = label.style;
        return [label.textContent, color, backgroundColor, borderRadius];
      });
    // Скругление метки — токен мелкого радиуса: без сверки порча имени токена выжила (В1б).
    const radius = cssVar('radiusControl');
    expect(statuses).toEqual([
      ['ОК', cssVar('ok'), cssVar('okTint'), radius],
      ['Внимание', cssVar('warn'), cssVar('warnTint'), radius],
      ['Ошибка', cssVar('crit'), cssVar('critTint'), radius],
    ]);
    const cells = [...table.getAllByRole('columnheader'), ...table.getAllByRole('cell')];
    expect(cells.map((cell) => cell.style.textAlign)).toEqual(Array(12).fill('center'));
    expect(document.body.textContent).not.toMatch(/скоро/i);
  });
});
