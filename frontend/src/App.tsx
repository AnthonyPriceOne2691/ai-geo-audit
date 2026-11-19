import { MantineProvider, createTheme } from '@mantine/core';
import type { ReactNode } from 'react';

import { StubPage } from './StubPage';

// Тема пока стандартная, своя приходит в Ч1.4б.
const theme = createTheme({});

/** Провайдеры — одни у приложения и у тестового рендера: тема и схема не расходятся (F9). */
export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <MantineProvider theme={theme} defaultColorScheme="auto">
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
