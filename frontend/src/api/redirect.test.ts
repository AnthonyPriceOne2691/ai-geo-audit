// @vitest-environment node
// F48: клиент не идёт по перенаправлению — два настоящих сервера на петле, порты свободные от
// системы; и ответ в виде браузера (`opaqueredirect`, код 0) — подставным объектом.
import { createServer, type RequestListener, type Server } from 'node:http';
import type { AddressInfo } from 'node:net';

import { afterEach, describe, expect, it } from 'vitest';

import { realFetch, useFetch } from '../test/server';
import { words } from '../words';

import { ApiError, getJson } from './client';

const servers: Server[] = [];
const anyBody = (body: unknown): body is unknown => body !== undefined;

afterEach(async () => {
  await Promise.all(
    servers.splice(0).map((s) => new Promise<void>((resolve) => s.close(() => resolve()))),
  );
});

/** Сервер на петле, порт — свободный от системы; адрес — `http://127.0.0.1:N`. */
function listen(handler: RequestListener): Promise<string> {
  const server = createServer(handler);
  servers.push(server);
  return new Promise((resolve) => {
    server.listen(0, '127.0.0.1', () => {
      resolve(`http://127.0.0.1:${(server.address() as AddressInfo).port}`);
    });
  });
}

/** F48: ответ на перенаправление — ошибка клиента; иное — красное здесь. */
async function redirectError(): Promise<ApiError> {
  const error: unknown = await getJson('/api/health', anyBody).then(
    () => undefined,
    (e: unknown) => e,
  );
  expect(error).toBeInstanceOf(ApiError);
  return error as ApiError;
}

describe('перенаправление', () => {
  it('F48: 302 на чужой узел — второй сервер запроса не видит, слова про перенаправление', async () => {
    let hits = 0;
    const second = await listen((_, res) => {
      hits += 1;
      res.writeHead(200, { 'content-type': 'application/json' }).end('{"status":"ok"}');
    });
    const first = await listen((_, res) => {
      res.writeHead(302, { location: `${second}/api/health` }).end();
    });
    let mode: RequestRedirect | undefined;
    useFetch((input, init) => {
      mode = init?.redirect;
      return realFetch(new URL(String(input), first), init);
    });
    const error = await redirectError();
    expect([error.kind, error.message, error.status]).toEqual([
      'redirect',
      words.api.redirect,
      302,
    ]);
    expect([hits, mode]).toEqual([0, 'manual']);
  });

  it('F48: ответ в виде браузера — opaqueredirect с кодом 0', async () => {
    useFetch(
      async () =>
        ({ type: 'opaqueredirect', status: 0, ok: false, headers: new Headers() }) as Response,
    );
    const error = await redirectError();
    expect([error.kind, error.message, error.status]).toEqual(['redirect', words.api.redirect, 0]);
  });
});
