// F32 (запрос кэша, который бросает), F49 (изменяющие запросы) и состояние сети и фокуса после теста
// (F35): общий клиент кэша — без повторов и без паузы без сети. Среда — jsdom: в Node у библиотеки
// свои умолчания (повторов 0), и тест не увидел бы умолчаний браузера, против которых он стоит.
import {
  focusManager,
  MutationObserver,
  onlineManager,
  QueryObserver,
} from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { createQueryClient } from './cache';

const settle = (ms = 30): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

afterEach(() => {
  vi.useRealTimers();
});

describe('F32, F49: общий клиент кэша', () => {
  it('F32: запрос, который бросает, зовётся один раз — повторов нет', async () => {
    vi.useFakeTimers();
    const client = createQueryClient();
    const queryFn = vi.fn(() => Promise.reject(new Error('список не пришёл')));
    const observer = new QueryObserver(client, { queryKey: ['список'], queryFn });
    const stop = observer.subscribe(() => undefined);
    await vi.advanceTimersByTimeAsync(10_000);
    stop();
    expect(queryFn).toHaveBeenCalledTimes(1);
    expect(observer.getCurrentResult().status).toBe('error');
  });

  it('F49: после offline изменяющий запрос уходит сразу, с online вызовов не прибавляется', async () => {
    const client = createQueryClient();
    client.mount();
    try {
      window.dispatchEvent(new Event('offline'));
      const mutationFn = vi.fn(() => Promise.resolve('записано'));
      const done = new MutationObserver(client, { mutationFn }).mutate();
      await settle();
      expect(mutationFn).toHaveBeenCalledTimes(1);
      window.dispatchEvent(new Event('online'));
      await settle();
      expect(mutationFn).toHaveBeenCalledTimes(1);
      await expect(done).resolves.toBe('записано');
    } finally {
      client.unmount();
    }
  });

  it('F49: изменяющий запрос со сбоем зовётся один раз — повторов нет', async () => {
    vi.useFakeTimers();
    const client = createQueryClient();
    const mutationFn = vi.fn(() => Promise.reject(new Error('не записалось')));
    const done = new MutationObserver(client, { mutationFn }).mutate().catch((e: unknown) => e);
    await vi.advanceTimersByTimeAsync(10_000);
    await expect(done).resolves.toBeInstanceOf(Error);
    expect(mutationFn).toHaveBeenCalledTimes(1);
  });
});

describe('F35: обвязка возвращает сеть и фокус библиотеки после каждого теста', () => {
  it('F35: тест уводит сеть и фокус', () => {
    onlineManager.setOnline(false);
    focusManager.setFocused(false);
    expect([onlineManager.isOnline(), focusManager.isFocused()]).toEqual([false, false]);
  });

  it('F35: следующий тест получает их назад', () => {
    expect([onlineManager.isOnline(), focusManager.isFocused()]).toEqual([true, true]);
  });
});
