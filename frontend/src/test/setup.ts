// Обвязка тестов: матчеры DOM, ожидание экрана, заглушка matchMedia, уборка после теста.
import '@testing-library/jest-dom/vitest';
import { cleanup, configure } from '@testing-library/react';
import { afterEach } from 'vitest';

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

afterEach(() => {
  cleanup();
});
