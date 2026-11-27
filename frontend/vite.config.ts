import { fileURLToPath } from 'node:url';

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
    // Путь от этого файла, а не от каталога запуска: гейт порч TS зовёт Stryker из корня
    // репозитория, и относительный путь Vitest искал бы там (проба среза 1).
    setupFiles: [fileURLToPath(new URL('./src/test/setup.ts', import.meta.url))],
    // Машина общая: рядом гоняют тесты другие сессии и соседние проекты (F2). CI поднимает
    // предел флагом `--maxWorkers`.
    maxWorkers: 2,
  },
});
