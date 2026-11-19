import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [react()],
  build: {
    // Карт исходников в сборке нет: оракул сборки краснеет на любом `.map` (F5).
    sourcemap: false,
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    // Машина общая: рядом гоняют тесты другие сессии и соседние проекты (F2). CI поднимает
    // предел флагом `--maxWorkers`.
    maxWorkers: 2,
  },
});
