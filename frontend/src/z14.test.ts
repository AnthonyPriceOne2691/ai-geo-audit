// @vitest-environment node
// A391: правило З14 описи защит — eslint проекта на образцах: HTML строкой в разметку — ошибка с
// именем правила; текст через `textContent` и JSX — без неё (UI_RULES.md, «Текст»).
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { ESLint } from 'eslint';
import { describe, expect, it } from 'vitest';

const FRONT = join(dirname(fileURLToPath(import.meta.url)), '..');
const eslint = new ESLint({ cwd: FRONT });

/** Правила, которые назвали нарушение З14 в образце: путь — внутри `src/`, как у кода фронта. */
async function z14(code: string): Promise<string[]> {
  const [result] = await eslint.lintText(code, { filePath: join(FRONT, 'src', 'z14-sample.tsx') });
  return (result?.messages ?? [])
    .filter((message) => message.message.startsWith('З14'))
    .map((message) => message.ruleId ?? '');
}

const SAMPLES = [
  [
    'dangerouslySetInnerHTML',
    'export const A = (p: { h: string }) => <div dangerouslySetInnerHTML={{ __html: p.h }} />;',
  ],
  ['el.innerHTML = …', 'export function a(el: HTMLElement, h: string) { el.innerHTML = h; }'],
  ['el.outerHTML = …', 'export function a(el: HTMLElement, h: string) { el.outerHTML = h; }'],
  [
    "el['innerHTML'] += …",
    "export function a(el: HTMLElement, h: string) { el['innerHTML'] += h; }",
  ],
  [
    'insertAdjacentHTML',
    "export function a(el: HTMLElement, h: string) { el.insertAdjacentHTML('beforeend', h); }",
  ],
  ['document.write', 'export function a(h: string) { document.write(h); }'],
  ['window.document.writeln', 'export function a(h: string) { window.document.writeln(h); }'],
] as const;

describe('правило З14 — текст в разметку только как текст', () => {
  it.each(SAMPLES)('A391: %s — ошибка eslint с именем правила', async (_, code) => {
    expect(await z14(code)).toEqual(['no-restricted-syntax']);
  });

  it('A391: текст через textContent и JSX — без ошибки З14', async () => {
    const code = [
      'export function a(el: HTMLElement, t: string) { el.textContent = t; }',
      'export const B = (p: { t: string }) => <p>{p.t}</p>;',
    ].join('\n');
    expect(await z14(code)).toEqual([]);
  });
});
