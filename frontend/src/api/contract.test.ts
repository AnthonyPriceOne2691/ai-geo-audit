// @vitest-environment node
// F24, F23, F29 [договор]: клиент на всех образцах файла `contracts/api-refusals.json` — каждый
// образец ведёт к своей строке таблицы, а строка таблицы без образца в файле — тоже красное.
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

import { answer } from '../test/server';

import { ApiError, type ApiErrorKind } from './client';
import { getHealth } from './health';

interface Sample {
  id: string;
  status: number;
  headers: Record<string, string>;
  body: { detail?: string; kind?: string; retry?: boolean; status?: string };
}

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const FILE = JSON.parse(readFileSync(join(ROOT, 'contracts', 'api-refusals.json'), 'utf8')) as {
  samples: Sample[];
  proxy: Sample[];
};
const SAMPLES = [...FILE.samples, ...FILE.proxy];

/** Вид ошибки клиента на каждый образец отказа; `health-ok` — не отказ, у него свой тест. */
const REFUSALS: Record<string, ApiErrorKind> = {
  'signed-out': 'signed-out',
  'not-found': 'server',
  'method-not-allowed': 'server',
  invalid: 'server',
  internal: 'server',
  unavailable: 'server',
  'too-large': 'server',
  busy: 'server',
  'proxy-too-large': 'server',
  'proxy-bad-gateway': 'server',
  'proxy-gateway-timeout': 'server',
};

const reply = (sample: Sample) => () =>
  new Response(JSON.stringify(sample.body), { status: sample.status, headers: sample.headers });

describe('клиент на образцах файла договора', () => {
  it('F24: каждый образец файла — в таблице теста, каждая строка таблицы — в файле', () => {
    const ids = SAMPLES.map((s) => s.id).filter((id) => id !== 'health-ok');
    expect(ids.sort()).toEqual(Object.keys(REFUSALS).sort());
  });

  it('F29: образец health-ok — здоровье отвечает', async () => {
    const sample = SAMPLES.find((s) => s.id === 'health-ok');
    if (!sample) throw new Error('в файле договора нет health-ok');
    answer('GET', '/api/health', reply(sample));
    await expect(getHealth()).resolves.toEqual({ status: 'ok' });
  });

  it.each(SAMPLES.filter((s) => s.id !== 'health-ok').map((s) => [s.id, s] as const))(
    'F24: образец %s — код, вид и признак повтора из тела, текст — detail целиком',
    async (id, sample) => {
      answer('GET', '/api/health', reply(sample));
      const error: unknown = await getHealth().then(
        () => undefined,
        (e: unknown) => e,
      );
      expect(error).toBeInstanceOf(ApiError);
      const { kind, status, message, serverKind, retry } = error as ApiError;
      expect({ kind, status, message, serverKind, retry }).toEqual({
        kind: REFUSALS[id],
        status: sample.status,
        message: sample.body.detail,
        serverKind: sample.body.kind,
        retry: sample.body.retry,
      });
    },
  );

  it('F23: signed-out — «входа нет» по признаку из заголовка', async () => {
    const sample = SAMPLES.find((s) => s.id === 'signed-out');
    if (!sample) throw new Error('в файле договора нет signed-out');
    answer('GET', '/api/health', reply(sample));
    const error = (await getHealth().then(
      () => undefined,
      (e: unknown) => e,
    )) as ApiError;
    expect([error.kind, error.refusal]).toEqual(['signed-out', 'signed-out']);
  });
});
