import { render, type RenderResult } from '@testing-library/react';
import type { ReactElement } from 'react';

import { AppProviders } from '../App';

/** Рендер для тестов — через те же провайдеры, что у приложения (F9). */
export function renderWithProviders(ui: ReactElement): RenderResult {
  return render(ui, { wrapper: AppProviders });
}
