// Обвязка тестов: матчеры DOM, ожидание экрана, заглушки matchMedia и ResizeObserver, уборка после
// теста.
import '@testing-library/jest-dom/vitest';
import { focusManager, onlineManager } from '@tanstack/react-query';
import { cleanup, configure } from '@testing-library/react';
import { afterEach, beforeEach } from 'vitest';

import { SCHEME_KEY } from '../theme/scheme';

import { checkMisses, installServer } from './server';

// Ожидание экрана — 3 с: раннер CI в несколько раз медленнее машины.
configure({ asyncUtilTimeout: 3000 });

// jsdom не знает matchMedia, а Mantine спрашивает его при монтировании. Заглушка отвечает
// «не совпало»: светлая схема и полное движение, пока тест не задаст своё.
if (typeof window !== 'undefined' && typeof window.matchMedia !== 'function') {
  window.matchMedia = (query: string): MediaQueryList => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  });
}

// jsdom не знает и ResizeObserver, а бегунок переключателя сегментами следит им за размером
// подписей. Заглушка не сообщает ничего: в jsdom у элементов нет размеров.
if (typeof window !== 'undefined' && typeof window.ResizeObserver !== 'function') {
  window.ResizeObserver = class {
    observe() {
      return undefined;
    }
    unobserve() {
      return undefined;
    }
    disconnect() {
      return undefined;
    }
  };
}

// Тестовый сервер на каждый тест: запрос мимо записанных ответов — промах, а не сеть (F30).
beforeEach(() => {
  installServer();
});

afterEach(() => {
  cleanup();
  // Менеджер схемы стоит в общих провайдерах: выбор одного теста иначе достался бы следующему.
  // Тесты в среде node окна не имеют.
  if (typeof window !== 'undefined') window.localStorage.removeItem(SCHEME_KEY);
  // Сеть и фокус у библиотеки кэша — одни на модуль: `offline` одного теста иначе достался бы
  // следующему (F35).
  onlineManager.setOnline(true);
  focusManager.setFocused(undefined);
  // Промахи — последними: их красное не срывает уборку выше (F30).
  checkMisses();
});
