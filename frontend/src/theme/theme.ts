import {
  Card,
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

// Ступени нейтральных палитр Mantine → роль токена в той схеме, где библиотека их берёт (F11): ими
// она красит рамки, линии, подложки и подписи компонентов. Таблица — та же, что в спеке «Схемы»;
// `gray` — в светлой схеме, `dark` — в тёмной. Белый библиотеки по схемам не переставляется.
const NEUTRAL_STEPS = {
  light: {
    'gray-0': 'sunken',
    'gray-1': 'sunken',
    'gray-2': 'line',
    'gray-3': 'line',
    'gray-4': 'edge',
    'gray-5': 'faint',
    'gray-6': 'faint',
    'gray-7': 'soft',
    'gray-8': 'ink',
    'gray-9': 'ink',
  },
  dark: {
    'dark-0': 'ink',
    'dark-1': 'soft',
    'dark-2': 'faint',
    'dark-3': 'edge',
    'dark-4': 'line',
    'dark-5': 'tint',
    'dark-6': 'surface',
    'dark-7': 'bg',
    'dark-8': 'sunken',
    'dark-9': 'bg',
  },
} as const;

const steps = (table: Readonly<Record<string, string>>): Record<string, string> =>
  Object.fromEntries(
    Object.entries(table).map(([step, role]) => [`--mantine-color-${step}`, cssVar(role)]),
  );

/** Ступени нейтральных палитр на токенах — отдельной функцией рядом с `cssVariablesResolver`:
 *  таблица ролей Ч1.4б и её тест не меняются, провайдеры сливают обе (F11). */
export const neutralStepsResolver: CSSVariablesResolver = () => ({
  variables: {},
  light: steps(NEUTRAL_STEPS.light),
  dark: steps(NEUTRAL_STEPS.dark),
});

/** Роли и ступени вместе — так их ставят провайдеры приложения и тестов (F9, F11). */
export const appVariablesResolver: CSSVariablesResolver = (theme) => {
  const [roles, neutral] = [cssVariablesResolver(theme), neutralStepsResolver(theme)];
  return {
    variables: { ...roles.variables, ...neutral.variables },
    light: { ...roles.light, ...neutral.light },
    dark: { ...roles.dark, ...neutral.dark },
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
    // Фон карточки — `surface` (F11): у Mantine он прямо `white` и `dark-6`, мимо переменных.
    Card: Card.extend({ styles: { root: { backgroundColor: cssVar('surface') } } }),
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
    // Переключатель сегментами. Бегунок и подписи — токеном 260 мс и общей кривой (F19): у Mantine
    // 200 мс, а кривая бегунка — прямо `ease`; при «меньше движения» токен — 0. Выбранное положение
    // — `surface`, выбранная подпись — `ink` (F11): у Mantine белый и `dark-5`, чёрный и белый.
    // Подложка, подписи и разделители — ступенями палитр. Подпись — переменной корня: её пишет
    // только пропс `color`, а его у переключателя нет.
    SegmentedControl: SegmentedControl.extend({
      vars: () => ({
        root: {
          '--sc-color': cssVar('surface'),
          '--sc-transition-duration': cssVar('motionSlow'),
          '--sc-transition-timing-function': cssVar('motionEase'),
        },
      }),
      styles: {
        root: { '--sc-label-color': cssVar('ink') },
        indicator: { transitionTimingFunction: cssVar('motionEase') },
      },
    }),
    // Таблица по центру умолчанием темы (F18): заголовок и ячейки, первая колонка тоже. Стиль
    // ложится на сам элемент — в месте вызова выравнивание не задаётся.
    // Цвета таблицы (F11): шапка — `sunken`, подписи колонок — `faint`, полоса строки — `sunken`;
    // подсветка строки — `tint` умолчанием `--table-hover-color`: пропс подсветки пишет свою
    // переменную, и она сильнее умолчания. Линии строк — ступенями палитр.
    Table: Table.extend({
      vars: () => ({ table: { '--table-striped-color': cssVar('sunken') } }),
      styles: {
        table: { '--table-hover-color': cssVar('tint') },
        thead: { backgroundColor: cssVar('sunken') },
        th: { textAlign: 'center', color: cssVar('faint') },
        td: { textAlign: 'center' },
      },
    }),
  },
});
