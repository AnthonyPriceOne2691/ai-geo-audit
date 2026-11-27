// @vitest-environment node
// F30: тестовый сервер помнит промах, а обвязка проверяет промахи после каждого теста: экран,
// проглотивший ошибку как «не загрузилось», теста не спасает.
import { describe, expect, it } from 'vitest';

import { checkMisses, missText } from './server';

describe('тестовый сервер — промахи', () => {
  it('F30: запрос без записанного ответа — красное с адресом, даже если ошибку проглотили', async () => {
    await fetch('/api/other').catch(() => undefined);
    expect(() => checkMisses()).toThrow('Ответ на "GET /api/other" не записан');
    expect(missText('GET', '/api/other')).toBe('Ответ на "GET /api/other" не записан');
  });

  // Тест обязан упасть: тело проходит, а обвязка после него находит промах (`setup.ts`).
  it.fails(
    'F30: обвязка проверяет промахи после теста — проглоченный промах роняет тест',
    async () => {
      await fetch('/api/other').catch(() => undefined);
    },
  );
});
