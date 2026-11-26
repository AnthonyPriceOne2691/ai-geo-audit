// A392: свойство — роль темы `[a-z][a-zA-Z]*` → `cssVar` → имя переменной → роль. Тема ссылается на
// токены `cssVar`, а тесты читают tokens.css `schemeColors`: оба перевода — рабочий код, круг даёт
// ту же роль на тысяче входов; порча обратного перевода — красное с контрпримером fast-check.
import fc from 'fast-check';
import { describe, expect, it } from 'vitest';

import { cssVar, schemeColors } from './tokens';

describe('имена токенов', () => {
  it('A392: роль → cssVar → имя переменной → роль — та же роль на 1000 входах', () => {
    fc.assert(
      fc.property(fc.stringMatching(/^[a-z][a-zA-Z]*$/), (role) => {
        const variable = /^var\((--audit-[a-z-]+)\)$/.exec(cssVar(role))?.[1] ?? '';
        const blocks = new Map([
          [`:root[data-mantine-color-scheme='light']`, { [variable]: '#000' }],
        ]);
        expect(Object.keys(schemeColors(blocks, 'light'))).toEqual([role]);
      }),
      { numRuns: 1000 },
    );
  });
});
