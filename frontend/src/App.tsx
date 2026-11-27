import { MantineProvider } from '@mantine/core';
import { QueryClientProvider } from '@tanstack/react-query';
import { useState, type ReactNode } from 'react';

import { createQueryClient } from './api/cache';
import { StubPage } from './StubPage';
import { schemeManager } from './theme/scheme';
import { appVariablesResolver, theme } from './theme/theme';

/** Провайдеры — одни у приложения и у тестового рендера: тема и схема не расходятся (F9). Роли и
 *  ступени нейтральных палитр — вместе (F11); выбор схемы — менеджером с ключом скрипта схемы
 *  (F12). Клиент кэша запросов — один на жизнь провайдеров: ленивым состоянием, а не в разметке,
 *  и новый рендер родителя не заводит нового кэша и второй проверки связи (F34). */
export function AppProviders({ children }: { children: ReactNode }) {
  const [client] = useState(createQueryClient);
  return (
    <QueryClientProvider client={client}>
      <MantineProvider
        theme={theme}
        cssVariablesResolver={appVariablesResolver}
        colorSchemeManager={schemeManager}
        defaultColorScheme="auto"
      >
        {children}
      </MantineProvider>
    </QueryClientProvider>
  );
}

export function App() {
  return (
    <AppProviders>
      <StubPage />
    </AppProviders>
  );
}
