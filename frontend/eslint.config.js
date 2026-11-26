// ESLint flat config (ESLint 9+).
// Шаблон канона CQG `<frontend>/eslint.config.js` с объявленной адаптацией (решение
// проверяющего 2026-11-18, В2; объявление в `scripts/lint/adapted.json` — с В1б):
// - блок З14 для `src/**/*.{ts,tsx}`: HTML строкой в разметку — ошибка (В1б, A391);
// - блок `scripts/**/*.mjs` с глобальными именами Node: оракул сборки — скрипт Node без
//   зависимостей, каталога скриптов в шаблоне нет;
// - блок `public/**/*.js` с глобальными именами браузера: скрипт схемы — классический скрипт до
//   приложения (F12), каталога `public/` в шаблоне нет. Остальное — как в шаблоне.
import js from '@eslint/js';
import ts from 'typescript-eslint';
import reactPlugin from 'eslint-plugin-react';
import reactHooks from 'eslint-plugin-react-hooks';
import importPlugin from 'eslint-plugin-import';
import prettier from 'eslint-config-prettier';
import globals from 'globals';

export default ts.config(
  {
    ignores: [
      'dist/**',
      'coverage/**',
      'node_modules/**',
      '*.config.js',
      '*.config.ts',
      'src/test/**',
    ],
  },

  js.configs.recommended,
  ...ts.configs.strict,
  ...ts.configs.stylistic,

  {
    files: ['src/**/*.{ts,tsx}'],
    plugins: { react: reactPlugin, 'react-hooks': reactHooks, import: importPlugin },
    languageOptions: {
      parserOptions: { ecmaFeatures: { jsx: true } },
      globals: {
        window: 'readonly',
        document: 'readonly',
        console: 'readonly',
        fetch: 'readonly',
        setTimeout: 'readonly',
        clearTimeout: 'readonly',
        localStorage: 'readonly',
        sessionStorage: 'readonly',
      },
    },
    settings: { react: { version: 'detect' } },
    rules: {
      // React 17+ автоматический JSX runtime — импорт React не нужен
      'react/react-in-jsx-scope': 'off',
      'react/jsx-uses-react': 'off',
      'react/prop-types': 'off', // используем TypeScript

      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',

      // unused vars с _ префиксом игнорируем
      '@typescript-eslint/no-unused-vars': [
        'error',
        { argsIgnorePattern: '^_', varsIgnorePattern: '^_', caughtErrorsIgnorePattern: '^_' },
      ],

      // warning-ратчет держит их суммой на нуле-в-росте (цель — 0)
      '@typescript-eslint/no-explicit-any': 'warn',
      '@typescript-eslint/no-non-null-assertion': 'warn',
      'no-console': ['warn', { allow: ['warn', 'error'] }],

      // Function-level limits
      'max-lines-per-function': ['warn', { max: 80, skipBlankLines: true, skipComments: true }],
      complexity: ['warn', 10],
      'max-depth': ['warn', 4],
      'max-params': ['warn', 5],

      'import/order': [
        'warn',
        {
          'newlines-between': 'always',
          groups: ['builtin', 'external', 'internal', 'parent', 'sibling', 'index'],
        },
      ],
    },
  },

  // Адаптация: правило З14 описи защит — HTML строкой в разметку нельзя, текст выводится только
  // как текст (UI_RULES.md, «Текст»; В1б, A391). Отдельным блоком: тело шаблона выше не тронуто.
  {
    files: ['src/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-syntax': [
        'error',
        ...[
          "JSXAttribute[name.name='dangerouslySetInnerHTML']",
          'AssignmentExpression > MemberExpression.left[property.name=/^(inner|outer)HTML$/]',
          'AssignmentExpression > MemberExpression.left[property.value=/^(inner|outer)HTML$/]',
          "CallExpression[callee.property.name='insertAdjacentHTML']",
          "CallExpression[callee.property.value='insertAdjacentHTML']",
          "CallExpression[callee.object.name='document'][callee.property.name=/^write(ln)?$/]",
          "CallExpression[callee.object.property.name='document'][callee.property.name=/^write(ln)?$/]",
          // Сверх образцов спеки — решение ведущей 2026-11-26: разбор строки в узлы, превью
          // страницы в рамке и присваивание свойств пачкой.
          'CallExpression[callee.property.name=/^(setHTMLUnsafe|createContextualFragment)$/]',
          'CallExpression[callee.property.value=/^(setHTMLUnsafe|createContextualFragment)$/]',
          "AssignmentExpression > MemberExpression.left[property.name='srcdoc']",
          "AssignmentExpression > MemberExpression.left[property.value='srcdoc']",
          "JSXAttribute[name.name='srcDoc']",
          "CallExpression[callee.object.name='Object'][callee.property.name='assign'] > ObjectExpression > Property[key.name=/^(inner|outer)HTML$/]",
          "CallExpression[callee.object.name='Object'][callee.property.name='assign'] > ObjectExpression > Property[key.value=/^(inner|outer)HTML$/]",
        ].map((selector) => ({
          selector,
          message: 'З14: HTML строкой в разметку нельзя — только текст (UI_RULES.md, «Текст»)',
        })),
      ],
    },
  },

  // Адаптация: скрипты Node — оракул сборки и его тест.
  {
    files: ['scripts/**/*.mjs'],
    languageOptions: { globals: globals.node },
  },

  // Адаптация: скрипт схемы из public/ — классический скрипт браузера до приложения (F12).
  {
    files: ['public/**/*.js'],
    languageOptions: { sourceType: 'script', globals: globals.browser },
  },

  // Prettier — ПОСЛЕДНИМ, чтобы отключить конфликтующие стилевые правила
  prettier,
);
