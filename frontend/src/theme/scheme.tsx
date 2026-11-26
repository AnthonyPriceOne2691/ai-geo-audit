import {
  SegmentedControl,
  localStorageColorSchemeManager,
  useMantineColorScheme,
  type MantineColorScheme,
} from '@mantine/core';
import { flushSync } from 'react-dom';

import { words } from '../words';

/** Ключ выбора схемы в хранилище браузера — один у скрипта `public/color-scheme.js` и у
 *  менеджера Mantine (F12): разойдутся — при запуске схема переключится. */
export const SCHEME_KEY = 'ai-geo-audit-color-scheme';

/** Менеджер схемы Mantine с общим ключом; выбор в другой вкладке он ловит событием хранилища
 *  (F13). */
export const schemeManager = localStorageColorSchemeManager({ key: SCHEME_KEY });

const CHOICES = ['light', 'dark', 'auto'] as const satisfies readonly MantineColorScheme[];

/** Смена схемы (F19): переходом документа, где он есть, — текст и фон меняются вместе, переход
 *  снимает страницу целиком; при «меньше движения» и без перехода — сразу. Признак «меньше
 *  движения» читается в момент смены. */
export function changeScheme(apply: () => void): void {
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (!reduce && typeof document.startViewTransition === 'function') {
    document.startViewTransition(() => {
      flushSync(apply);
    });
  } else {
    apply();
  }
}

function isChoice(value: string): value is MantineColorScheme {
  return (CHOICES as readonly string[]).includes(value);
}

/** Переключатель «Светлая / Тёмная / Как в системе» — сегментами, как в образце владельца. Пропса
 *  `color` нет: он красит выбранную подпись белым (`autoContrast`), в светлой схеме — белое на
 *  белом. Повторный выбор того же положения ничего не меняет: Mantine зовёт `onChange` только по
 *  событию `change` радиокнопки, а у выбранного положения его нет (тест F19). */
export function SchemeSwitch() {
  const { colorScheme, setColorScheme } = useMantineColorScheme();
  const { scheme } = words;
  return (
    <SegmentedControl
      aria-label={scheme.label}
      value={colorScheme}
      data={CHOICES.map((choice) => ({ value: choice, label: scheme[choice] }))}
      onChange={(value) => {
        if (!isChoice(value)) return;
        changeScheme(() => {
          setColorScheme(value);
        });
      }}
    />
  );
}
