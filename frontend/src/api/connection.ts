// Проверка связи (Ч1.4д): итог — данными; сбой клиента — тоже итог, а не ошибка запроса.
import { ApiError } from './client';
import { getHealth } from './health';

/** Итог проверки связи: сервис ответил — или текст клиента, почему ответа нет. */
export type Connection = { ok: true } | { ok: false; text: string };

/** Ключ проверки связи в кэше запросов. */
export const CONNECTION_KEY = ['connection'] as const;

/**
 * Функция запроса проверки связи: сбой клиента — итогом, и прежний итог остаётся на экране, пока
 * едет новый (F36); бросает только отмену вызывающего — экран ушёл, итога нет (F28).
 */
export async function checkConnection(signal: AbortSignal): Promise<Connection> {
  try {
    await getHealth({ signal });
    return { ok: true };
  } catch (error) {
    if (error instanceof ApiError) return { ok: false, text: error.message };
    throw error;
  }
}
