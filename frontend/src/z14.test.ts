// @vitest-environment node
// A391: правило З14 описи защит — eslint проекта на образцах: HTML строкой в разметку — ошибка с
// именем правила; текст через `textContent` и JSX — без неё (UI_RULES.md, «Текст»). Образец линтится
// и в корне `src/`, и во вложенном каталоге: порча области правила выводит один из путей из-под
// правила (находка 4 второй проверяющей).
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { ESLint } from 'eslint';
import { describe, expect, it } from 'vitest';

const FRONT = join(dirname(fileURLToPath(import.meta.url)), '..');
const eslint = new ESLint({ cwd: FRONT });

/** Пути образца: `.tsx` для JSX, иначе ещё и вложенный `.ts`. */
const paths = (jsx: boolean): string[] =>
  jsx
    ? ['src/z14-sample.tsx', 'src/theme/z14-sample.tsx']
    : ['src/z14-sample.tsx', 'src/theme/z14-sample.ts'];

/** Нарушения З14 по путям образца: путь, правило и строгость (2 — ошибка). */
async function z14(code: string, jsx: boolean): Promise<string[][]> {
  const found: string[][] = [];
  for (const path of paths(jsx)) {
    const [result] = await eslint.lintText(code, { filePath: join(FRONT, path) });
    for (const message of result?.messages ?? []) {
      if (message.message.startsWith('З14')) {
        found.push([path, message.ruleId ?? '', String(message.severity)]);
      }
    }
  }
  return found;
}

const fn = (body: string): string => `export function a(el: HTMLElement, h: string) { ${body} }`;

const SAMPLES: [string, string, boolean][] = [
  [
    'dangerouslySetInnerHTML',
    'export const A = (p: { h: string }) => <div dangerouslySetInnerHTML={{ __html: p.h }} />;',
    true,
  ],
  ['el.innerHTML = …', fn('el.innerHTML = h;'), false],
  ['el.outerHTML = …', fn('el.outerHTML = h;'), false],
  ["el['innerHTML'] += …", fn("el['innerHTML'] += h;"), false],
  ['insertAdjacentHTML', fn("el.insertAdjacentHTML('beforeend', h);"), false],
  ['document.write', fn('document.write(h);'), false],
  ['window.document.writeln', fn('window.document.writeln(h);'), false],
  // Сверх образцов спеки — решение ведущей 2026-11-26 (находка второй проверяющей).
  ['setHTMLUnsafe', fn('el.setHTMLUnsafe(h);'), false],
  [
    'createContextualFragment',
    fn('el.append(document.createRange().createContextualFragment(h));'),
    false,
  ],
  ['frame.srcdoc = …', fn('(el as HTMLIFrameElement).srcdoc = h;'), false],
  [
    '<iframe srcDoc>',
    'export const F = (p: { h: string }) => <iframe title="t" srcDoc={p.h} />;',
    true,
  ],
  ['Object.assign innerHTML', fn('Object.assign(el, { innerHTML: h });'), false],
  ["Object.assign 'outerHTML'", fn("Object.assign(el, { 'outerHTML': h });"), false],
  // Заметки второй проверяющей к находке 4 — решение ведущей 2026-11-26: формы через скобки и
  // `setAttribute`, образец на каждый селектор.
  ["el['setHTMLUnsafe'](…)", fn("el['setHTMLUnsafe'](h);"), false],
  ["frame['srcdoc'] = …", fn("(el as HTMLIFrameElement)['srcdoc'] = h;"), false],
  ["setAttribute('srcdoc', …)", fn("el.setAttribute('srcdoc', h);"), false],
  ["el['setAttribute']('srcDoc', …)", fn("el['setAttribute']('srcDoc', h);"), false],
];

describe('правило З14 — текст в разметку только как текст', () => {
  it.each(SAMPLES)('A391: %s — ошибка eslint с именем правила', async (_, code, jsx) => {
    const wanted = paths(jsx).map((path) => [path, 'no-restricted-syntax', '2']);
    expect(await z14(code, jsx)).toEqual(wanted);
  });

  it('A391: текст через textContent и JSX, разбор DOMParser и вставка узлов — без ошибки З14', async () => {
    // DOMParser с вставкой узлов не запрещён: разбор инертен, а вставка разобранных узлов — путь
    // для очищенного содержимого (принятый риск, решение ведущей 2026-11-26).
    const code = [
      fn('el.textContent = h;'),
      'export const B = (p: { t: string }) => <p>{p.t}</p>;',
      "export function c(el: HTMLElement, h: string) { el.append(...new DOMParser().parseFromString(h, 'text/html').body.childNodes); }",
    ].join('\n');
    expect(await z14(code, true)).toEqual([]);
  });
});
