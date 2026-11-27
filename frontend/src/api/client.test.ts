// @vitest-environment node
// F21–F23, F25–F29, F48: клиент API на подменном `fetch` тестового сервера — путь, заголовки и режимы
// запроса; отказы по коду и по признаку; обрыв, молчание и отмена; ответ не сервиса; ряд 3xx.
import { afterEach, describe, expect, it, vi } from 'vitest';

import { answer, json, requests } from '../test/server';
import { words } from '../words';

import { ApiError, getJson, TIMEOUT_MS } from './client';
import { getHealth } from './health';

const anyBody = (body: unknown): body is unknown => body !== undefined;

/** Ошибка клиента из промиса — для сверки видом, кодом и текстом. */
async function failure(promise: Promise<unknown>): Promise<ApiError> {
  const error: unknown = await promise.then(
    () => undefined,
    (e: unknown) => e,
  );
  if (!(error instanceof ApiError)) throw new Error(`ждали ApiError, пришло: ${String(error)}`);
  return error;
}

const stream = (start: (c: ReadableStreamDefaultController<Uint8Array>) => void) => () =>
  new Response(new ReadableStream<Uint8Array>({ start }), {
    status: 200,
    headers: { 'content-type': 'application/json' },
  });

const silence = (): Promise<Response> => new Promise<Response>(() => undefined);

describe('F21: путь и запрос', () => {
  it('F21: GET без Authorization и пропуска, no-store, same-origin, manual, путь от корня', async () => {
    answer('GET', '/api/health', json(200, { status: 'ok' }));
    await getHealth();
    const seen = requests();
    expect(seen.map((r) => [r.method, r.path])).toEqual([['GET', '/api/health']]);
    expect(seen[0]?.headers.has('authorization')).toBe(false);
    expect(seen[0]?.headers.has('x-access-token')).toBe(false);
    expect(seen[0]?.headers.get('accept')).toBe('application/json');
    expect([seen[0]?.init.cache, seen[0]?.init.credentials, seen[0]?.init.redirect]).toEqual([
      'no-store',
      'same-origin',
      'manual',
    ]);
  });

  it.each([
    '//узел/x',
    'https://other.example/api/x',
    'api/health',
    '/health',
    '/api/../x',
    '/api/%2e%2e/x',
    '/api/a\\b',
  ])('F21: путь %s — отказ до запроса', async (path) => {
    answer('GET', path, json(200, { status: 'ok' }));
    const error = await failure(getJson(path, anyBody));
    expect([error.name, error.kind, error.message]).toEqual(['ApiError', 'path', words.api.path]);
    expect(requests()).toHaveLength(0);
  });

  it.each(['/api/..c', '/api/d..', '/api/x?next=../y'])(
    'F21: путь %s — шагов «.» и «..» в нём нет, запрос уходит',
    async (path) => {
      answer('GET', path, json(200, { status: 'ok' }));
      await getJson(path, anyBody);
      expect(requests().map((r) => r.path)).toEqual([path]);
    },
  );
});

describe('F22, F23: пароль прокси, «входа нет» и отказ', () => {
  it.each([
    ['с вызовом Basic', { 'www-authenticate': 'Basic realm="audit"' }],
    ['без вызова', {}],
  ])('F22: 401 %s — пароль сервера', async (_, headers: Record<string, string>) => {
    answer('GET', '/api/health', () => new Response('', { status: 401, headers }));
    const error = await failure(getHealth());
    expect([error.kind, error.message, error.status]).toEqual([
      'proxy-password',
      words.api.proxyPassword,
      401,
    ]);
  });

  it.each([
    ['signed-out с другим текстом', 'signed-out', 'Сессия закончилась', 'signed-out'],
    ['permission', 'permission', 'Нет права на это действие', 'refused'],
    ['без признака', null, 'Нет доступа', 'refused'],
    ['незнакомый признак', 'quota', 'Предел исчерпан', 'refused'],
    [
      'другой признак с текстом «входа нет»',
      'permission',
      'Нужен вход — войдите заново',
      'refused',
    ],
  ])('F23: 403, %s — разбор по признаку, текст сервера', async (_, marker, detail, kind) => {
    const headers: Record<string, string> = marker ? { 'x-access-refusal': marker } : {};
    answer('GET', '/api/health', json(403, { detail, kind: 'forbidden', retry: false }, headers));
    const error = await failure(getHealth());
    expect([error.kind, error.message, error.refusal, error.status]).toEqual([
      kind,
      detail,
      marker,
      403,
    ]);
  });
});

const PAGE = '<html><body><h1>Bad Gateway</h1><hr>nginx/1.24.0 на host-7</body></html>';
const FORMLESS: [string, () => Response][] = [
  ['страница прокси', () => new Response(PAGE, { headers: { 'content-type': 'text/html' } })],
  ['пустое тело', () => new Response('')],
  ['JSON не по форме', json(200, { error: 'Bad Gateway', host: 'host-7' })],
];
const CODES: [number, (code: number) => string][] = [
  [413, words.api.tooLarge],
  [429, words.api.tooMany],
  [502, words.api.down],
  [503, words.api.down],
  [504, words.api.down],
  [500, words.api.failed],
  [501, words.api.failed],
  [505, words.api.failed],
  [418, words.api.unexpected],
];
const FORMLESS_ROWS = CODES.flatMap(([code, text]) =>
  FORMLESS.map(([name, make]) => [code, name, make, text(code)] as const),
);

describe('F25: отказ без формы — слова по коду', () => {
  it.each(FORMLESS_ROWS)('F25: %i, %s — слова по коду', async (code, _, make, text) => {
    answer('GET', '/api/health', async () => {
      const base = make();
      return new Response(await base.text(), { status: code, headers: base.headers });
    });
    const error = await failure(getHealth());
    expect([error.kind, error.status, error.message]).toEqual(['server', code, text]);
    expect(error.message).not.toMatch(/nginx|host-7|Bad Gateway/);
  });

  const ALMOST: [string, unknown][] = [
    ['null', null],
    ['без detail', { kind: 'internal', retry: false }],
    ['без kind', { detail: 'Сбой', retry: false }],
    ['без retry', { detail: 'Сбой', kind: 'internal' }],
    ['retry строкой', { detail: 'Сбой', kind: 'internal', retry: 'false' }],
  ];

  it.each(ALMOST)('F25: 500, тело почти по форме — %s — слова по коду', async (_, body) => {
    answer('GET', '/api/health', json(500, body));
    const error = await failure(getHealth());
    expect([error.kind, error.status, error.message]).toEqual([
      'server',
      500,
      words.api.failed(500),
    ]);
  });
});

describe('F26–F28: обрыв, молчание, отмена', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('F26: сеть отклоняет запрос — ответа нет, кода нет', async () => {
    answer('GET', '/api/health', () => {
      throw new TypeError('Failed to fetch');
    });
    const error = await failure(getHealth());
    expect([error.kind, error.message, error.status]).toEqual([
      'no-response',
      words.api.noResponse,
      null,
    ]);
  });

  it('F26: связь рвётся посреди тела — ответа нет', async () => {
    answer(
      'GET',
      '/api/health',
      stream((c) => {
        c.enqueue(new TextEncoder().encode('{"sta'));
        c.error(new TypeError('terminated'));
      }),
    );
    const error = await failure(getHealth());
    expect([error.kind, error.message]).toEqual(['no-response', words.api.noResponse]);
  });

  it('F27: предел по умолчанию — 15 с, одной константой', () => {
    expect(TIMEOUT_MS).toBe(15_000);
  });

  it('F27: сервер молчит — «не ответил за 15 с», запрос оборван', { timeout: 1000 }, async () => {
    vi.useFakeTimers();
    answer('GET', '/api/health', silence);
    const pending = failure(getHealth());
    await vi.advanceTimersByTimeAsync(TIMEOUT_MS);
    const error = await pending;
    expect([error.kind, error.message]).toEqual(['timeout', words.api.timeout(15)]);
    expect(requests()[0]?.init.signal?.aborted).toBe(true);
  });

  it('F27: заголовки пришли, тело замолчало — «не ответил»', { timeout: 1000 }, async () => {
    vi.useFakeTimers();
    answer(
      'GET',
      '/api/health',
      stream((c) => c.enqueue(new TextEncoder().encode('{"sta'))),
    );
    const pending = failure(getHealth());
    await vi.advanceTimersByTimeAsync(TIMEOUT_MS);
    expect((await pending).kind).toBe('timeout');
  });

  it(
    'F27: свой сигнал вызова предела не снимает; число — из предела',
    { timeout: 1000 },
    async () => {
      vi.useFakeTimers();
      answer('GET', '/api/health', silence);
      const own = new AbortController();
      const pending = failure(
        getJson('/api/health', anyBody, { signal: own.signal, timeoutMs: 5000 }),
      );
      await vi.advanceTimersByTimeAsync(5000);
      const error = await pending;
      expect([error.kind, error.message]).toEqual(['timeout', words.api.timeout(5)]);
    },
  );

  it('F28: отмена вызывающего — AbortError как есть, не обрыв и не предел', async () => {
    answer('GET', '/api/health', silence);
    const own = new AbortController();
    const pending = getHealth({ signal: own.signal }).then(
      () => undefined,
      (e: unknown) => e,
    );
    own.abort();
    const error = await pending;
    expect(error).not.toBeInstanceOf(ApiError);
    expect((error as Error).name).toBe('AbortError');
  });
});

describe('F27, F28: отмена до вызова и уборка после ответа', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('F28: отменён до вызова — запроса нет, отмена как есть', async () => {
    answer('GET', '/api/health', json(200, { status: 'ok' }));
    const own = new AbortController();
    own.abort();
    const error: unknown = await getHealth({ signal: own.signal }).then(
      () => undefined,
      (e: unknown) => e,
    );
    expect(error).toBe(own.signal.reason);
    expect(requests()).toHaveLength(0);
  });

  it('F27: ответ пришёл — предел снят, таймеров не осталось', async () => {
    vi.useFakeTimers();
    answer('GET', '/api/health', json(200, { status: 'ok' }));
    await getHealth();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('F28: ответ пришёл — поздняя отмена вызывающего запрос не трогает', async () => {
    answer('GET', '/api/health', json(200, { status: 'ok' }));
    const own = new AbortController();
    await getHealth({ signal: own.signal });
    own.abort();
    expect(requests()[0]?.init.signal?.aborted).toBe(false);
  });
});

describe('F29: ответил не сервис', () => {
  it.each([
    [
      'HTML с кодом 200',
      () =>
        new Response('<!doctype html><title>Vite</title>', {
          headers: { 'content-type': 'text/html' },
        }),
    ],
    [
      'JSON с типом text/plain',
      () => new Response('{"status":"ok"}', { headers: { 'content-type': 'text/plain' } }),
    ],
    [
      'application/json, который не разбирается',
      () => new Response('{"status":', { headers: { 'content-type': 'application/json' } }),
    ],
    ['application/json другого вида', json(200, { ok: true })],
    ['application/json null', json(200, null)],
  ])('F29: %s — «ответил не сервис»', async (_, reply: () => Response) => {
    answer('GET', '/api/health', reply);
    const error = await failure(getHealth());
    expect([error.kind, error.message, error.status]).toEqual([
      'not-service',
      words.api.notService,
      200,
    ]);
  });

  it.each([
    [
      'HTML с кодом 200',
      () => new Response('<!doctype html>', { headers: { 'content-type': 'text/html' } }),
    ],
    [
      'application/json, который не разбирается',
      () => new Response('{"status":', { headers: { 'content-type': 'application/json' } }),
    ],
  ])('F29: %s, вид берёт всё — «ответил не сервис»', async (_, reply: () => Response) => {
    answer('GET', '/api/x', reply);
    const error = await failure(getJson('/api/x', (_body: unknown): _body is unknown => true));
    expect(error.kind).toBe('not-service');
  });

  it.each(['application/json; charset=utf-8', 'Application/JSON ; charset=UTF-8'])(
    'F29: тип %s, разбор и вид сошлись — здоровье отвечает',
    async (type) => {
      answer('GET', '/api/health', json(200, { status: 'ok' }, { 'content-type': type }));
      await expect(getHealth()).resolves.toEqual({ status: 'ok' });
    },
  );
});

describe('F48: перенаправление — весь ряд 3xx и только он', () => {
  it.each([
    [300, 'redirect', words.api.redirect],
    [399, 'redirect', words.api.redirect],
    [400, 'server', words.api.unexpected(400)],
  ])('F48: ответ %i — вид %s', async (code, kind, text) => {
    answer('GET', '/api/health', () => new Response(null, { status: code }));
    const error = await failure(getHealth());
    expect([error.kind, error.status, error.message]).toEqual([kind, code, text]);
  });
});
