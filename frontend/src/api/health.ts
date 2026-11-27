// Здоровье сервиса: `GET /api/health` ждёт тело образца `health-ok` файла договора (F29, [договор]).
import { getJson, type GetOptions } from './client';

export interface Health {
  status: 'ok';
}

const isHealth = (body: unknown): body is Health =>
  typeof body === 'object' && body !== null && (body as { status?: unknown }).status === 'ok';

/** Проверка связи: ответ сервиса нужного вида или ошибка клиента словами. */
export const getHealth = (options: GetOptions = {}): Promise<Health> =>
  getJson('/api/health', isHealth, options);
