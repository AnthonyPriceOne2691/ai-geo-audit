import { MantineProvider } from '@mantine/core';
import type { ReactNode } from 'react';

import { StubPage } from './StubPage';
import { schemeManager } from './theme/scheme';
import { appVariablesResolver, theme } from './theme/theme';

/** Провайдеры — одни у приложения и у тестового рендера: тема и схема не расходятся (F9). Роли и
 *  ступени нейтральных палитр — вместе (F11); выбор схемы — менеджером с ключом скрипта схемы
 *  (F12). */
export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <MantineProvider
      theme={theme}
      cssVariablesResolver={appVariablesResolver}
      colorSchemeManager={schemeManager}
      defaultColorScheme="auto"
    >
      {children}
    </MantineProvider>
  );
}

export function App() {
  return (
    <AppProviders>
      <StubPage />
    </AppProviders>
  );
}
