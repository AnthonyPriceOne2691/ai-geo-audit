// @vitest-environment node
// F37–F39: сервер разработки из `vite.config.ts` — порты из переменных с проверкой значения,
// `strictPort`, прокси `/api` без переписывания пути и ответ web на недоступный API [договор];
// слушает только свою машину. Порты — свободные от системы: 5177 живого стенда тест не занимает.
import type { AddressInfo } from 'node:net';
import { fileURLToPath } from 'node:url';

import { createServer, resolveConfig, type ProxyOptions, type ViteDevServer } from 'vite';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { sample } from './test/contract';
import { realFetch } from './test/server';
import { closeServers, freePort, listen } from './test/sockets';

const FRONT = fileURLToPath(new URL('..', import.meta.url));
const CONFIG = fileURLToPath(new URL('../vite.config.ts', import.meta.url));
const started: ViteDevServer[] = [];

beforeEach(() => {
  // Переменные машины тест не наследует: без них — номера по умолчанию.
  vi.stubEnv('FRONT_PORT', undefined);
  vi.stubEnv('API_PORT', undefined);
});

afterEach(async () => {
  await Promise.all(started.splice(0).map((server) => server.close()));
  await closeServers();
  vi.unstubAllEnvs();
});

/** Конфиг из файла, разобранный заново с этими переменными окружения — как при старте `vite`. */
async function configWith(env: Record<string, string>) {
  for (const [name, value] of Object.entries(env)) vi.stubEnv(name, value);
  vi.resetModules();
  return (await import('../vite.config')).default;
}

/** Разобранный Vite конфиг файла: так его видят сервер разработки и `vite preview`. */
const resolved = () =>
  resolveConfig({ configFile: CONFIG, root: FRONT, logLevel: 'silent' }, 'serve');

/** Цель прокси `/api` в конфиге. */
const target = (config: Awaited<ReturnType<typeof configWith>>): unknown =>
  (config.server?.proxy?.['/api'] as ProxyOptions | undefined)?.target;

/** Сервер разработки из файла конфига с этими переменными; закрывается после теста. */
async function startDev(env: Record<string, string>): Promise<ViteDevServer> {
  for (const [name, value] of Object.entries(env)) vi.stubEnv(name, value);
  const server = await createServer({ configFile: CONFIG, root: FRONT, logLevel: 'silent' });
  started.push(server);
  return server.listen();
}

describe('F37: порты сервера разработки', () => {
  it('F37: без переменных — фронт 5177 у разработки и preview, цель прокси — API на 8107', async () => {
    const config = await configWith({});
    expect([config.server?.port, config.preview?.port]).toEqual([5177, 5177]);
    expect(target(config)).toBe('http://127.0.0.1:8107');
  });

  it('F37: FRONT_PORT и API_PORT — их номера', async () => {
    const config = await configWith({ FRONT_PORT: '6177', API_PORT: '9107' });
    expect([config.server?.port, config.preview?.port]).toEqual([6177, 6177]);
    expect(target(config)).toBe('http://127.0.0.1:9107');
  });

  it.each([
    ['FRONT_PORT', ''],
    ['FRONT_PORT', '0'],
    ['FRONT_PORT', 'пять'],
    ['API_PORT', '0'],
    ['API_PORT', '65536'],
    ['API_PORT', '80.5'],
  ])('F37: %s=%j — отказ с именем переменной', async (name, value) => {
    await expect(configWith({ [name]: value })).rejects.toThrow(name);
  });

  it('F37: strictPort у разработки, у preview — наследством; порт preview задан явно', async () => {
    const config = await resolved();
    expect([config.server.strictPort, config.preview.strictPort, config.preview.port]).toEqual([
      true,
      true,
      5177,
    ]);
  });

  it('F37: порт фронта занят — отказ старта с номером порта, а не переезд на соседний', async () => {
    const busy = await listen(() => undefined);
    const api = await freePort();
    await expect(
      startDev({ FRONT_PORT: String(busy.port), API_PORT: String(api) }),
    ).rejects.toThrow(String(busy.port));
  });
});

describe('F38: прокси /api к API', () => {
  it('F38: GET /api/health через порт фронта — у API тот же путь, ответ без правок', async () => {
    const health = sample('health-ok');
    const paths: (string | undefined)[] = [];
    const api = await listen((req, res) => {
      paths.push(req.url);
      res.writeHead(health.status, health.headers).end(JSON.stringify(health.body));
    });
    const front = await freePort();
    await startDev({ FRONT_PORT: String(front), API_PORT: String(api.port) });
    const response = await realFetch(`http://127.0.0.1:${front}/api/health`);
    expect([response.status, await response.json()]).toEqual([health.status, health.body]);
    expect(paths).toEqual(['/api/health']);
  });

  it('F38: API не слушает — 502 телом образца proxy-bad-gateway, как web в бою', async () => {
    const gateway = sample('proxy-bad-gateway');
    const front = await freePort();
    await startDev({ FRONT_PORT: String(front), API_PORT: String(await freePort()) });
    const response = await realFetch(`http://127.0.0.1:${front}/api/health`);
    expect(response.status).toBe(gateway.status);
    expect(response.headers.get('content-type')).toBe(gateway.headers['content-type']);
    expect(await response.json()).toEqual(gateway.body);
  });
});

describe('F39: только своя машина, защиты Vite не ослаблены', () => {
  it('F39: разработка и preview — 127.0.0.1; cors, allowedHosts и fs.strict — как у Vite', async () => {
    const ours = await resolved();
    const base = await resolveConfig(
      { configFile: false, root: FRONT, logLevel: 'silent' },
      'serve',
    );
    expect([ours.server.host, ours.preview.host]).toEqual(['127.0.0.1', '127.0.0.1']);
    expect([ours.server.cors, ours.server.fs.strict]).toEqual([base.server.cors, true]);
    expect(base.server.fs.strict).toBe(true);
    // Заданный узел `server.host` Vite 7.3.7 кладёт в `allowedHosts` сам (проба среза 3), а адреса
    // IP пускает и без списка (`isHostAllowedInternal`). Сверх этого имён быть не должно.
    for (const hosts of [ours.server.allowedHosts, ours.preview.allowedHosts]) {
      expect(hosts).not.toBe(true);
      expect((hosts as string[]).filter((name) => name !== '127.0.0.1')).toEqual(
        base.server.allowedHosts,
      );
    }
  });

  it('F39: запущенный сервер разработки слушает 127.0.0.1', async () => {
    const front = await freePort();
    const server = await startDev({
      FRONT_PORT: String(front),
      API_PORT: String(await freePort()),
    });
    expect((server.httpServer?.address() as AddressInfo).address).toBe('127.0.0.1');
  });
});
