import {
  Checkbox,
  Container,
  Paper,
  Radio,
  SegmentedControl,
  Table,
  Text,
  createTheme,
  defaultVariantColorsResolver,
  type CSSVariablesResolver,
  type VariantColorsResolver,
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

// Роли палитры основного цвета: ими Mantine красит флажок, вкладки и виды кнопок. Имена — от
// `theme.primaryColor`; синий библиотеки дал бы тексту на светлой поверхности 3,56 : 1.
const PALETTE_ROLES = {
  filled: 'primary',
  'filled-hover': 'primary',
  light: 'tint',
  'light-hover': 'tint',
  'light-color': 'accent',
  outline: 'accent',
  'outline-hover': 'tint',
  text: 'accent',
} as const;

/** Ссылки одни в обеих схемах, но ставятся в обе: умолчания Mantine стоят по схемам и иначе
 *  перебили бы общий блок. */
export const cssVariablesResolver: CSSVariablesResolver = (theme) => {
  const palette = Object.entries(PALETTE_ROLES).map(
    ([part, role]) => [`--mantine-color-${theme.primaryColor}-${part}`, role] as const,
  );
  const schemeVariables = Object.fromEntries(
    [...Object.entries(SCHEME_ROLES), ...palette].map(([name, role]) => [name, cssVar(role)]),
  );
  return {
    variables: {},
    light: schemeVariables,
    dark: schemeVariables,
  };
};

/** Подпись на заливке основного цвета — `onPrimary`: умолчание Mantine — белый библиотеки, а он в
 *  тёмной схеме на `primary` даёт 1,99 : 1. Остальные виды и цвета — как у умолчания. */
export const variantColorResolver: VariantColorsResolver = (input) => {
  const colors = defaultVariantColorsResolver(input);
  const own = input.variant === 'filled' && input.color === input.theme.primaryColor;
  return own ? { ...colors, color: cssVar('onPrimary') } : colors;
};

/** Значок на заливке флажка и радио — та же подпись; у контурного вида и своего цвета значка —
 *  как у библиотеки. */
const iconOnFill =
  (name: string) => (_theme: unknown, props: { variant?: string; iconColor?: unknown }) => ({
    root: props.variant === 'outline' || props.iconColor ? {} : { [name]: cssVar('onPrimary') },
  });

export const theme = createTheme({
  // Без флага Mantine двигает свои появления и при «меньше движения» в системе (F14).
  respectReducedMotion: true,
  variantColorResolver,
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
    // стилей темы и перекрывает щит.
    Text: Text.extend({ styles: { root: { '--text-color': 'currentColor' } } }),
    Checkbox: Checkbox.extend({ vars: iconOnFill('--checkbox-icon-color') }),
    Radio: Radio.extend({ vars: iconOnFill('--radio-icon-color') }),
    // Бегунок и подписи переключателя — токеном 260 мс и общей кривой (F19): у Mantine 200 мс, а
    // кривая бегунка — прямо `ease`. При «меньше движения» токен — 0, движения нет.
    SegmentedControl: SegmentedControl.extend({
      vars: () => ({
        root: {
          '--sc-transition-duration': cssVar('motionSlow'),
          '--sc-transition-timing-function': cssVar('motionEase'),
        },
      }),
      styles: { indicator: { transitionTimingFunction: cssVar('motionEase') } },
    }),
    // Таблица по центру умолчанием темы (F18): заголовок и ячейки, первая колонка тоже. Стиль
    // ложится на сам элемент — в месте вызова выравнивание не задаётся.
    Table: Table.extend({
      styles: { th: { textAlign: 'center' }, td: { textAlign: 'center' } },
    }),
  },
});
