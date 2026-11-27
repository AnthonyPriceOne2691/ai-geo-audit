// F31–F36 и половины F24, F28, F29 о заглушке: блок «Связь с сервером» на общем клиенте кэша —
// состояние словами в области role="status", повтор только кнопкой, прежний ответ — пока едет новый.
// Тексты блока — точными строками спеки: поиск подстроки дал бы ложный зелёный (F31).
import { type QueryClient, useQueryClient } from '@tanstack/react-query';
import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { TIMEOUT_MS } from './api/client';
import { App, AppProviders } from './App';
import { StubPage } from './StubPage';
import { reply, sample } from './test/contract';
import { answer, json, requests } from './test/server';
import { words } from './words';

const ASKING = 'Спрашиваем сервер…';
const OK = 'Сервер отвечает';
const RETRY = 'Спросить ещё раз';
const UNAVAILABLE = sample('unavailable').body.detail ?? '';

const status = (): string | null => screen.getByRole('status').textContent;
const retry = (): HTMLElement => screen.getByRole('button', { name: RETRY });
const tick = (ms = 30): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));
const dropped = (): never => {
  throw new TypeError('Failed to fetch');
};

/** Ответ по команде теста: видно, что на экране, пока ответа нет. */
function gate() {
  let settle: (make: () => Response) => void = () => undefined;
  const pending = new Promise<Response>((resolve, reject) => {
    settle = (make) => {
      try {
        resolve(make());
      } catch (error) {
        reject(error as Error);
      }
    };
  });
  return { reply: () => pending, settle };
}

afterEach(() => {
  vi.useRealTimers();
});

describe('F31: состояние проверки связи — словами в области role="status"', () => {
  const PAGE = '<html><body><h1>502 Bad Gateway</h1><hr>nginx на host-7</body></html>';
  it.each([
    ['образец здоровья', reply(sample('health-ok')), OK],
    ['ответа нет', dropped, words.api.noResponse],
    ['401', () => new Response('', { status: 401 }), words.api.proxyPassword],
    [
      '502 страницей прокси',
      () => new Response(PAGE, { status: 502, headers: { 'content-type': 'text/html' } }),
      words.api.down(502),
    ],
    ['образец 503', reply(sample('unavailable')), UNAVAILABLE],
  ])('F31: %s — до ответа «Спрашиваем сервер…», затем точный текст', async (_, make, text) => {
    const late = gate();
    answer('GET', '/api/health', late.reply);
    render(<App />);
    expect(status()).toBe(ASKING);
    act(() => late.settle(make));
    await waitFor(() => expect(status()).toBe(text));
    expect(document.body.textContent).not.toMatch(/ApiError|TypeError|Failed to fetch|nginx/);
  });

  it('F31: сервер молчит дольше предела — «не ответил» с числом предела', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    answer('GET', '/api/health', () => new Promise<Response>(() => undefined));
    render(<App />);
    expect(status()).toBe(ASKING);
    await act(() => vi.advanceTimersByTimeAsync(TIMEOUT_MS));
    await waitFor(() => expect(status()).toBe(words.api.timeout(15)));
  });
});

describe('F32, F33, F35, F36: повтор только кнопкой, один на два нажатия, прежний ответ', () => {
  it('F32: 503, возврат во вкладку и сеть пропала и появилась — запрос один', async () => {
    answer('GET', '/api/health', reply(sample('unavailable')));
    render(<App />);
    await waitFor(() => expect(status()).toBe(UNAVAILABLE));
    act(() => {
      window.dispatchEvent(new Event('visibilitychange'));
      window.dispatchEvent(new Event('offline'));
      window.dispatchEvent(new Event('online'));
    });
    await tick();
    expect(requests()).toHaveLength(1);
    await userEvent.setup().click(retry());
    await waitFor(() => expect(requests()).toHaveLength(2));
  });

  it('F33: два нажатия в одном такте — один новый запрос, пока ждём — кнопка в загрузке', async () => {
    answer('GET', '/api/health', reply(sample('health-ok')));
    render(<App />);
    await waitFor(() => expect(status()).toBe(OK));
    const late = gate();
    answer('GET', '/api/health', late.reply);
    act(() => {
      retry().click();
      retry().click();
    });
    await waitFor(() => expect(retry()).toBeDisabled());
    expect(retry()).toHaveAttribute('data-loading');
    expect(requests()).toHaveLength(2);
    act(() => late.settle(reply(sample('health-ok'))));
    await waitFor(() => expect(retry()).toBeEnabled());
    expect(requests()).toHaveLength(2);
  });

  it('F35: после offline проверка уходит и кончается «Ответа нет…»; пока ждём — в загрузке', async () => {
    answer('GET', '/api/health', reply(sample('health-ok')));
    render(<App />);
    await waitFor(() => expect(status()).toBe(OK));
    act(() => {
      window.dispatchEvent(new Event('offline'));
    });
    const late = gate();
    answer('GET', '/api/health', late.reply);
    await userEvent.setup().click(retry());
    await waitFor(() => expect(retry()).toBeDisabled());
    expect(requests()).toHaveLength(2);
    act(() => late.settle(dropped));
    await waitFor(() => expect(status()).toBe(words.api.noResponse));
  });

  it('F36: «Ответа нет…», затем повтор — прежний ответ на экране, кнопка в загрузке', async () => {
    answer('GET', '/api/health', dropped);
    render(<App />);
    await waitFor(() => expect(status()).toBe(words.api.noResponse));
    const late = gate();
    answer('GET', '/api/health', late.reply);
    await userEvent.setup().click(retry());
    await waitFor(() => expect(retry()).toBeDisabled());
    expect(status()).toBe(words.api.noResponse);
    act(() => late.settle(reply(sample('health-ok'))));
    await waitFor(() => expect(status()).toBe(OK));
  });
});

describe('F34: клиент кэша — один на жизнь провайдеров', () => {
  it('F34: родитель отрисовал App заново — клиент кэша тот же, проверка не уходит снова', async () => {
    answer('GET', '/api/health', reply(sample('health-ok')));
    const { rerender } = render(<App />);
    await waitFor(() => expect(status()).toBe(OK));
    rerender(<App />);
    await tick();
    expect(status()).toBe(OK);
    expect(requests()).toHaveLength(1);
  });

  // Запрос второй раз не уходит и при клиенте, созданном в разметке: `useQuery` держит наблюдателя
  // в состоянии и к новому клиенту не переходит (ручная порча P7 выжила). Поэтому — сам клиент.
  it('F34: провайдеры отрисованы заново — клиент кэша тот же по ссылке', async () => {
    answer('GET', '/api/health', reply(sample('health-ok')));
    const seen: QueryClient[] = [];
    function Probe() {
      seen.push(useQueryClient());
      return null;
    }
    const tree = () => (
      <AppProviders>
        <StubPage />
        <Probe />
      </AppProviders>
    );
    const { rerender } = render(tree());
    await waitFor(() => expect(status()).toBe(OK));
    rerender(tree());
    expect(seen.length).toBeGreaterThan(1);
    expect(new Set(seen).size).toBe(1);
  });
});

describe('F24, F28, F29 на заглушке', () => {
  it('F24: detail с разметкой — текстом, элемента img в документе нет', async () => {
    const detail = '<img src=x onerror=alert(1)>';
    answer('GET', '/api/health', json(503, { detail, kind: 'unavailable', retry: true }));
    render(<App />);
    await waitFor(() => expect(status()).toBe(detail));
    expect(document.querySelector('img')).toBeNull();
  });

  it('F28: заглушку убрали посреди проверки и вернули — запрос отменён, «Спрашиваем сервер…»', async () => {
    answer('GET', '/api/health', () => new Promise<Response>(() => undefined));
    const { rerender } = render(
      <AppProviders>
        <StubPage />
      </AppProviders>,
    );
    expect(status()).toBe(ASKING);
    rerender(<AppProviders>{null}</AppProviders>);
    await tick();
    expect(requests()[0]?.init.signal?.aborted).toBe(true);
    rerender(
      <AppProviders>
        <StubPage />
      </AppProviders>,
    );
    expect(status()).toBe(ASKING);
  });

  it('F29: 200 с HTML — «Ответил не сервис», «Сервер отвечает» на заглушке нет', async () => {
    answer(
      'GET',
      '/api/health',
      () =>
        new Response('<!doctype html><title>Vite</title>', {
          headers: { 'content-type': 'text/html' },
        }),
    );
    render(<App />);
    await waitFor(() => expect(status()).toBe(words.api.notService));
    expect(screen.queryByText(OK)).toBeNull();
  });
});
