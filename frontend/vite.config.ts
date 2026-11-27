import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

/** Порты разработки по умолчанию — живой стенд: фронт 5177, API 8107 (README). */
const FRONT_DEFAULT = 5177;
const API_DEFAULT = 8107;
/** Своя машина: сервер разработки и `vite preview` наружу не слушают (F39). */
const HOST = '127.0.0.1';

/**
 * Порт из переменной окружения: нет её — свой номер; пустое, «0», не число и вне 1–65535 — отказ
 * с именем переменной: порт 0 Vite молча меняет на 5173, и фронт ушёл бы с адреса стенда (F37).
 */
function port(name: string, fallback: number): number {
  const raw = process.env[name];
  if (raw === undefined) return fallback;
  const value = /^\d+$/.test(raw) ? Number(raw) : 0;
  if (value < 1 || value > 65_535) {
    throw new Error(`${name}=${JSON.stringify(raw)}: нужен номер порта от 1 до 65535`);
  }
  return value;
}

/**
 * Ответ на недоступный API — как у web Ч1.5б в бою: 502 телом образца `proxy-bad-gateway` файла
 * договора (F38). Файл читается при старте прокси, а не при загрузке конфига: сборка web видит
 * только `frontend/` (`deploy/web/Dockerfile`).
 */
function badGateway(): { status: number; headers: Record<string, string>; body: string } {
  const path = fileURLToPath(new URL('../contracts/api-refusals.json', import.meta.url));
  const file = JSON.parse(readFileSync(path, 'utf8')) as {
    proxy: { id: string; status: number; headers: Record<string, string>; body: unknown }[];
  };
  const sample = file.proxy.find((s) => s.id === 'proxy-bad-gateway');
  if (!sample) throw new Error('в файле договора нет образца proxy-bad-gateway');
  return { status: sample.status, headers: sample.headers, body: JSON.stringify(sample.body) };
}

const frontPort = port('FRONT_PORT', FRONT_DEFAULT);
const apiPort = port('API_PORT', API_DEFAULT);

export default defineConfig({
  plugins: [react()],
  build: {
    // Карт исходников в сборке нет: оракул сборки краснеет на любом `.map` (F5).
    sourcemap: false,
  },
  server: {
    host: HOST,
    port: frontPort,
    // Занятый порт — отказ старта с номером, а не переезд на соседний (F37).
    strictPort: true,
    proxy: {
      // Путь не переписывается: API ждёт `/api/…` так же, как за web (F38).
      '/api': {
        target: `http://${HOST}:${apiPort}`,
        configure: (proxy) => {
          const reply = badGateway();
          proxy.on('error', (_error, _req, res) => {
            if (!('writeHead' in res) || res.headersSent) return;
            res.writeHead(reply.status, reply.headers).end(reply.body);
          });
        },
      },
    },
  },
  // Узел, `strictPort` и прокси `preview` наследует от `server`; порт — явно: свой у Vite — 4173.
  preview: { port: frontPort },
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
