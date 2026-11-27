// Общий клиент кэша запросов (Ч1.4д): один на жизнь приложения, в общем провайдере `App.tsx`.
import { QueryClient } from '@tanstack/react-query';

/**
 * Клиент кэша с умолчаниями сервиса вместо умолчаний библиотеки: сам не повторяет — ни на сбой,
 * ни на возврат во вкладку, ни на появление сети; запрос уходит, даже если браузер считает, что
 * сети нет, — сбой виден сразу словами клиента. Изменяющие запросы — те же правила: без сети не
 * встают на паузу и не уходят сами с её появлением (F32, F35, F49; умолчания — decisions, срез 0).
 */
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        refetchOnWindowFocus: false,
        refetchOnReconnect: false,
        networkMode: 'always',
      },
      mutations: { retry: false, networkMode: 'always' },
    },
  });
}
