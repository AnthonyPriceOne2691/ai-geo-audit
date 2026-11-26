// Схема до первой отрисовки (F12). Выбор из хранилища: «светлая» и «тёмная» — как есть; «как в
// системе», пусто, чужое значение и сбой хранилища — по системе. Атрибут у корня ставится раньше,
// чем браузер разберёт <body>: под ним стоят блоки токенов. Ключ — тот же, что у менеджера схемы
// Mantine (`src/theme/scheme.tsx`), тест F12 сверяет их. Подключается обычным блокирующим тегом
// в <head>: модуль, `defer` или `async` исполнились бы после разбора страницы.
(function () {
  const KEY = 'ai-geo-audit-color-scheme';
  let stored = null;
  try {
    // В Chrome при запрете хранилища бросает уже само обращение к свойству.
    stored = window.localStorage.getItem(KEY);
  } catch {
    stored = null;
  }
  const dark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  const scheme = stored === 'light' || stored === 'dark' ? stored : dark ? 'dark' : 'light';
  document.documentElement.setAttribute('data-mantine-color-scheme', scheme);
})();
