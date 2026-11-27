// @vitest-environment node
// F24, F23, F29 [договор]: клиент на всех образцах файла `contracts/api-refusals.json` — каждый
// образец ведёт к своей строке таблицы, а строка таблицы без образца в файле — тоже красное.
import { describe, expect, it } from 'vitest';

import { reply, sample as named, SAMPLES } from '../test/contract';
import { answer } from '../test/server';

import { ApiError, type ApiErrorKind } from './client';
import { getHealth } from './health';

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

describe('клиент на образцах файла договора', () => {
  it('F24: каждый образец файла — в таблице теста, каждая строка таблицы — в файле', () => {
    const ids = SAMPLES.map((s) => s.id).filter((id) => id !== 'health-ok');
    expect(ids.sort()).toEqual(Object.keys(REFUSALS).sort());
  });

  it('F29: образец health-ok — здоровье отвечает', async () => {
    answer('GET', '/api/health', reply(named('health-ok')));
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
    answer('GET', '/api/health', reply(named('signed-out')));
    const error = (await getHealth().then(
      () => undefined,
      (e: unknown) => e,
    )) as ApiError;
    expect([error.kind, error.refusal]).toEqual(['signed-out', 'signed-out']);
  });
});
