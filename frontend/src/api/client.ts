// Клиент API (Ч1.4в): один путь фронта к нашему `/api/`. Отказы, обрывы, молчание и чужие ответы
// он называет словами, и экран показывает их как есть (F21–F30, F48). `Authorization` не трогает:
// в нём браузер несёт пароль сайта; пропуск Ч1.10 ляжет сюда же и не должен уйти мимо `/api/`.
import { words } from '../words';

/** Предел ожидания ответа по умолчанию; у web — 30 с, клиент обрывает раньше (F27). */
export const TIMEOUT_MS = 15_000;

export type ApiErrorKind =
  | 'path'
  | 'proxy-password'
  | 'signed-out'
  | 'refused'
  | 'server'
  | 'redirect'
  | 'no-response'
  | 'timeout'
  | 'not-service';

interface Details {
  status?: number;
  refusal?: string | null;
  serverKind?: string;
  retry?: boolean;
}

/** Ошибка клиента: вид, текст для человека и то, что из ответа прочтут экраны дальше. */
export class ApiError extends Error {
  override readonly name = 'ApiError';
  readonly kind: ApiErrorKind;
  readonly status: number | null;
  readonly refusal: string | null;
  readonly serverKind: string | null;
  readonly retry: boolean | null;

  constructor(kind: ApiErrorKind, message: string, details: Details = {}) {
    super(message);
    this.kind = kind;
    this.status = details.status ?? null;
    this.refusal = details.refusal ?? null;
    this.serverKind = details.serverKind ?? null;
    this.retry = details.retry ?? null;
  }
}

export interface GetOptions {
  signal?: AbortSignal;
  timeoutMs?: number;
}

/** Общая форма отказа сервиса [договор]: `{detail, kind, retry}`. */
interface Refusal {
  detail: string;
  kind: string;
  retry: boolean;
}

const isRefusal = (body: unknown): body is Refusal =>
  typeof body === 'object' &&
  body !== null &&
  typeof (body as Refusal).detail === 'string' &&
  typeof (body as Refusal).kind === 'string' &&
  typeof (body as Refusal).retry === 'boolean';

/** Шаг пути `.` или `..`, и в коде процента: URL их схлопнет и уведёт путь (F21). */
const DOT_STEP = /^(?:\.|%2e){1,2}$/i;

/** Только наш путь: от `/api/`, без узла, обратной черты и шагов назад — иначе отказ до запроса. */
function checkPath(path: string): void {
  const steps = (path.split(/[?#]/)[0] ?? '').split('/');
  if (!path.startsWith('/api/') || path.includes('\\') || steps.some((s) => DOT_STEP.test(s))) {
    throw new ApiError('path', words.api.path);
  }
}

const seconds = (ms: number): number => Math.round(ms / 1000);

/** Отмена по сигналу: сразу, если он уже отменён, иначе — по событию; возврат снимает слушателя. */
function whenAborted(signal: AbortSignal, run: () => void): () => void {
  if (signal.aborted) {
    run();
    return () => undefined;
  }
  signal.addEventListener('abort', run);
  return () => signal.removeEventListener('abort', run);
}

/**
 * Свой сигнал запроса: отмена вызывающего и свой таймер предела собраны вручную — `AbortSignal.any`
 * нет в jsdom и у браузеров нижней границы, а таймер двигают управляемые часы теста (F27, F28).
 * Причину своя отмена не несёт: что случилось, `failure()` читает по самим сигналам.
 */
function linkSignals(caller: AbortSignal | undefined, timeoutMs: number) {
  const controller = new AbortController();
  const off = caller ? whenAborted(caller, () => controller.abort()) : () => undefined;
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const done = (): void => {
    clearTimeout(timer);
    off();
  };
  return { signal: controller.signal, done };
}

/** Тело ответа под тем же сигналом: обрыв и предел посреди тела — как до ответа (F26, F27). */
function readText(response: Response, signal: AbortSignal): Promise<string> {
  return new Promise((resolve, reject) => {
    const off = whenAborted(signal, () => reject(signal.reason));
    response.text().then(resolve, reject).finally(off);
  });
}

const NOT_JSON = Symbol('not-json');

/** Тело как JSON — только при типе `application/json` и удачном разборе (F29). */
async function jsonBody(response: Response, signal: AbortSignal): Promise<unknown> {
  const type = (response.headers.get('content-type') ?? '').split(';')[0]?.trim().toLowerCase();
  if (type !== 'application/json') return NOT_JSON;
  const text = await readText(response, signal);
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return NOT_JSON;
  }
}

const isRedirect = (response: Response): boolean =>
  response.type === 'opaqueredirect' || (response.status >= 300 && response.status < 400);

/** Слова по коду для отказа без формы — страница прокси, пустое тело, чужой JSON (F25). */
function byCode(status: number): string {
  if (status === 413) return words.api.tooLarge(status);
  if (status === 429) return words.api.tooMany(status);
  if (status >= 502 && status <= 504) return words.api.down(status);
  if (status >= 500) return words.api.failed(status);
  return words.api.unexpected(status);
}

/** Отказ: 401 — всегда пароль прокси; 403 — по признаку; общая форма — текст сервера (F22–F25). */
function refusalError(response: Response, body: unknown): ApiError {
  const { status } = response;
  if (status === 401) return new ApiError('proxy-password', words.api.proxyPassword, { status });
  if (!isRefusal(body)) return new ApiError('server', byCode(status), { status });
  const details = { status, serverKind: body.kind, retry: body.retry };
  if (status !== 403) return new ApiError('server', body.detail, details);
  const refusal = response.headers.get('x-access-refusal');
  const kind = refusal === 'signed-out' ? 'signed-out' : 'refused';
  return new ApiError(kind, body.detail, { ...details, refusal });
}

/** Сбой: отмена вызывающего — как есть, предел, обрыв связи; прочее — как есть (F26–F28). */
function failure(error: unknown, caller: AbortSignal | undefined, own: AbortSignal, ms: number) {
  if (caller?.aborted) return caller.reason as unknown;
  if (own.aborted) return new ApiError('timeout', words.api.timeout(seconds(ms)));
  if (error instanceof TypeError) return new ApiError('no-response', words.api.noResponse);
  return error;
}

/** `GET` к нашему API: данные нужного вида — или ошибка клиента словами. */
export async function getJson<T>(
  path: string,
  isShape: (body: unknown) => body is T,
  options: GetOptions = {},
): Promise<T> {
  checkPath(path);
  const timeoutMs = options.timeoutMs ?? TIMEOUT_MS;
  const link = linkSignals(options.signal, timeoutMs);
  try {
    const response = await fetch(path, {
      method: 'GET',
      cache: 'no-store',
      credentials: 'same-origin',
      redirect: 'manual',
      headers: { Accept: 'application/json' },
      signal: link.signal,
    });
    if (isRedirect(response)) {
      throw new ApiError('redirect', words.api.redirect, { status: response.status });
    }
    const body = await jsonBody(response, link.signal);
    if (!response.ok) throw refusalError(response, body);
    if (body === NOT_JSON || !isShape(body)) {
      throw new ApiError('not-service', words.api.notService, { status: response.status });
    }
    return body;
  } catch (error) {
    throw failure(error, options.signal, link.signal, timeoutMs);
  } finally {
    link.done();
  }
}
