import '@mantine/core/styles.css';
// Токены красят страницу до запуска React (F12).
import './theme/tokens.css';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import { App } from './App';

const root = document.getElementById('root');
if (!root) {
  throw new Error('В index.html нет элемента #root: приложению некуда отрисоваться');
}

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
