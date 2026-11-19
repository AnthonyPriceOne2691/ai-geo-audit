import {
  Container,
  Paper,
  Table,
  Text,
  createTheme,
  type CSSVariablesResolver,
} from '@mantine/core';

import { cssVar } from './tokens';

// Переменные схемы Mantine → роль токена. Значения — ссылками на tokens.css: цвет схемы
// подставляет браузер, а в расчёты цвета Mantine OKLCH не попадает — его darken и lighten читают
// OKLCH как чёрный. Поэтому theme.colors, white и black остаются библиотечными.
const SCHEME_ROLES = {
  '--mantine-color-body': 'surface',
  '--mantine-color-text': 'ink',
  '--mantine-color-bright': 'ink',
  '--mantine-color-dimmed': 'faint',
  '--mantine-color-placeholder': 'faint',
  '--mantine-color-anchor': 'accent',
  '--mantine-color-error': 'crit',
  '--mantine-color-default': 'surface',
  '--mantine-color-default-hover': 'tint',
  '--mantine-color-default-color': 'ink',
  '--mantine-color-default-border': 'edge',
  '--mantine-primary-color-filled': 'primary',
  // Под курсором заливка та же, как в образце владельца: отклик — светом, а не цветом библиотеки.
  '--mantine-primary-color-filled-hover': 'primary',
  '--mantine-primary-color-contrast': 'onPrimary',
  '--mantine-primary-color-light': 'tint',
  '--mantine-primary-color-light-hover': 'tint',
  '--mantine-primary-color-light-color': 'accent',
} as const;

const schemeVariables = Object.fromEntries(
  Object.entries(SCHEME_ROLES).map(([name, role]) => [name, cssVar(role)]),
);

/** Ссылки одни в обеих схемах, но ставятся в обе: умолчания Mantine стоят по схемам и иначе
 *  перебили бы общий блок. */
export const cssVariablesResolver: CSSVariablesResolver = () => ({
  variables: {},
  light: schemeVariables,
  dark: schemeVariables,
});

export const theme = createTheme({
  // Без флага Mantine двигает свои появления и при «меньше движения» в системе (F14).
  respectReducedMotion: true,
  fontFamily: cssVar('font'),
  headings: { fontFamily: cssVar('font') },
  // Скруглений два: карточка и мелкое — кнопка, поле, метка статуса. Все ступени Mantine
  // разложены по ним, чтобы умолчание библиотеки нигде не всплыло.
  radius: {
    xs: cssVar('radiusControl'),
    sm: cssVar('radiusControl'),
    md: cssVar('radiusControl'),
    lg: cssVar('radiusCard'),
    xl: cssVar('radiusCard'),
  },
  defaultRadius: 'md',
  components: {
    // Card рисуется через Paper: умолчание здесь даёт карточке её 16 px.
    Paper: Paper.extend({ defaultProps: { radius: 'lg' } }),
    // Рабочая область одна на все экраны: не шире 1360 px вместе с полями по 28 px.
    Container: Container.extend({
      vars: () => ({ root: { '--container-size': cssVar('workspace') } }),
      styles: { root: { paddingInline: cssVar('gutter') } },
    }),
    // Щит цвета текста (F10). У Text цвет — `var(--text-color)` без запасного значения, а
    // переменную он ставит себе только при пропсе цвета. Переменная наследуется: расширение
    // браузера, задавшее её выше по дереву, перекрасило бы каждый Text без пропса.
    // `currentColor` возвращает задуманное «как у родителя». Переменная пропса ложится позже
    // стилей темы, а без пропса щит и не ставится.
    Text: Text.extend({
      styles: (_theme, props) => ({ root: props.color ? {} : { '--text-color': 'currentColor' } }),
    }),
    // Таблица по центру умолчанием темы (F18): заголовок и ячейки, первая колонка тоже. Стиль
    // ложится на сам элемент — в месте вызова выравнивание не задаётся.
    Table: Table.extend({
      styles: { th: { textAlign: 'center' }, td: { textAlign: 'center' } },
    }),
  },
});
