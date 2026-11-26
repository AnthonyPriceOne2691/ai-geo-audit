import { MantineProvider } from '@mantine/core';
import type { ReactNode } from 'react';

import { StubPage } from './StubPage';
import { schemeManager } from './theme/scheme';
import { cssVariablesResolver, theme } from './theme/theme';

/** Провайдеры — одни у приложения и у тестового рендера: тема и схема не расходятся (F9). Выбор
 *  схемы — менеджером с ключом скрипта схемы (F12). */
export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <MantineProvider
      theme={theme}
      cssVariablesResolver={cssVariablesResolver}
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
