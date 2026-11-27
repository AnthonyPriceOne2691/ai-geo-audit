// Тестовый сервер: подменяет `fetch` на каждый тест (обвязка, `setup.ts`), отвечает записанными
// ответами, пишет запросы, рвёт ответ по сигналу и помнит промахи — после теста они красные (F30).
export interface Seen {
  method: string;
  path: string;
  headers: Headers;
  init: RequestInit;
}

type Reply = () => Response | Promise<Response>;

const replies = new Map<string, Reply>();
const seen: Seen[] = [];
const missed: string[] = [];
const original = globalThis.fetch;

/** Настоящий `fetch` — для тестов на настоящих серверах (F48). */
export const realFetch: typeof fetch = (input, init) => original(input, init);

const key = (method: string, path: string): string => `${method} ${path}`;

/** Текст промаха: запрос, на который ответ не записан. */
export const missText = (method: string, path: string): string =>
  `Ответ на "${key(method, path)}" не записан`;

function pathOf(input: RequestInfo | URL): string {
  if (typeof input === 'string') return input;
  return input instanceof URL ? `${input.pathname}${input.search}` : input.url;
}

/** Ответ под сигналом запроса: отмена рвёт ожидание той же причиной, что у настоящего `fetch`. */
function underSignal(work: Promise<Response>, signal?: AbortSignal | null): Promise<Response> {
  if (!signal) return work;
  return new Promise((resolve, reject) => {
    signal.addEventListener('abort', () => reject(signal.reason as Error), { once: true });
    work.then(resolve, reject);
  });
}

const fakeFetch: typeof fetch = async (input, init = {}) => {
  // Отменённый до вызова запрос не уходит — как у настоящего `fetch`: отказ причиной, записи нет.
  if (init.signal?.aborted) throw init.signal.reason as Error;
  const method = (init.method ?? 'GET').toUpperCase();
  const path = pathOf(input);
  seen.push({ method, path, headers: new Headers(init.headers), init });
  const reply = replies.get(key(method, path));
  if (!reply) {
    missed.push(missText(method, path));
    throw new Error(missText(method, path));
  }
  return underSignal(Promise.resolve().then(reply), init.signal);
};

/** Подменить `fetch`: обвязка — перед каждым тестом, тест на настоящих серверах — своим (F48). */
export function useFetch(fn: typeof fetch): void {
  Object.defineProperty(globalThis, 'fetch', { value: fn, configurable: true, writable: true });
}

/** Перед тестом: подменный `fetch` с пустой записью. */
export function installServer(): void {
  useFetch(fakeFetch);
}

/** Записать ответ на запрос. */
export function answer(method: string, path: string, reply: Reply): void {
  replies.set(key(method, path), reply);
}

/** Ответ JSON с кодом и заголовками. */
export const json =
  (status: number, body: unknown, headers: Record<string, string> = {}) =>
  (): Response =>
    new Response(JSON.stringify(body), {
      status,
      headers: { 'content-type': 'application/json', ...headers },
    });

/** Запросы, дошедшие до тестового сервера, по порядку. */
export const requests = (): readonly Seen[] => seen;

/** После теста: настоящий `fetch` назад, запись — заново; промахи — красное с адресами. */
export function checkMisses(): void {
  const lost = missed.splice(0);
  replies.clear();
  seen.length = 0;
  useFetch(original);
  if (lost.length > 0) throw new Error(lost.join('; '));
}
