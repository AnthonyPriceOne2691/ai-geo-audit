// @vitest-environment node
// Оракул сборки: чистый образец зелёный; копия с одной порчей красная и называет свою причину —
// по одному коду 1 не видно, какая проверка сработала.
import { spawnSync } from 'node:child_process';
import {
  cpSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { afterAll, beforeAll, describe, expect, it } from 'vitest';

const ORACLE = join(dirname(fileURLToPath(import.meta.url)), 'check-dist.mjs');
const NAME = 'Пробный сервис';
const JS = 'assets/index-B7xQ2_Lm.js';
const VENDOR = 'assets/vendor-Cq2fP0aZ.js';
const CSS = 'assets/index-Dk3-Q9Zr.css';
// Шрифт — файлы двух наборов знаков, как их кладёт Vite (F6).
const LATIN = 'assets/geologica-latin-wght-normal-Lt4Xa9Qe.woff2';
const CYRILLIC = 'assets/geologica-cyrillic-wght-normal-Cy7Rb2Kd.woff2';
// Фон и текст схем в файле токенов — и они же в CSS сборки так, как их пишет минификатор: числа без
// ведущего нуля, без кавычек в селекторе (F12).
const TOKENS = `:root[data-mantine-color-scheme='light'] {
  --audit-bg: oklch(97% 0.006 250);
  --audit-ink: oklch(23% 0.020 255);
}
:root[data-mantine-color-scheme='dark'] {
  --audit-bg: oklch(17% 0.012 255);
  --audit-ink: oklch(95% 0.006 250);
}
`;
const LIGHT =
  ':root[data-mantine-color-scheme=light]' +
  '{--audit-bg: oklch(97% .006 250);--audit-ink: oklch(23% .02 255)}';
const DARK =
  ':root[data-mantine-color-scheme=dark]' +
  '{--audit-bg: oklch(17% .012 255);--audit-ink: oklch(95% .006 250)}';
const PAGE = ':root body{background-color:var(--audit-bg);color:var(--audit-ink)}';
// Сборка-образец — как у Vite: страница, свой код, код пакетов с узлами из списка, стили.
const SAMPLE = {
  'index.html': `<!doctype html>
<html lang="ru">
  <head>
    <meta charset="UTF-8" />
    <title>${NAME}</title>
    <script type="module" crossorigin src="/${JS}"></script>
    <link rel="modulepreload" crossorigin href="/${VENDOR}">
    <link rel="stylesheet" crossorigin href="/${CSS}">
  </head>
  <body><div id="root"></div></body>
</html>
`,
  [JS]: 'import"./vendor-Cq2fP0aZ.js";document.title="x";',
  [VENDOR]: 'const s="http://www.w3.org/2000/svg",r="https://react.dev/",m="https://mantine.dev/";',
  [CSS]: `body{margin:0}${LIGHT}${DARK}${PAGE}.logo{background:url(./logo-AbCd1234.svg)}`,
  'assets/logo-AbCd1234.svg': '<svg></svg>',
  [LATIN]: 'wOF2',
  [CYRILLIC]: 'wOF2',
};
// Куда подкладывается порча и какой файл оракул обязан назвать.
const PLACES = {
  head: ['index.html', (html, text) => html.replace('</head>', `${text}\n</head>`)],
  body: ['index.html', (html, text) => html.replace('</body>', `${text}\n</body>`)],
  css: [CSS, (css, text) => `${text}\n${css}`],
  js: [VENDOR, (js, text) => `${js}${text}`],
};

const dirs = [];
let clean = '';

function tempDir() {
  const dir = mkdtempSync(join(tmpdir(), 'check-dist-'));
  dirs.push(dir);
  return dir;
}

function edit(dist, path, change) {
  writeFileSync(join(dist, path), change(readFileSync(join(dist, path), 'utf8')));
}

/** Оракул по копии образца с одной порчей: код выхода и весь вывод. */
function oracle(spoil = () => undefined) {
  const dir = tempDir();
  cpSync(clean, dir, { recursive: true });
  spoil(join(dir, 'dist'));
  const args = [ORACLE, join(dir, 'dist'), join(dir, 'brand.ts'), join(dir, 'tokens.css')];
  const run = spawnSync(process.execPath, args, { encoding: 'utf8' });
  return { code: run.status, out: run.stdout + run.stderr };
}

// F5: порча краснит оракул, и он называет свою причину строкой «файл: причина».
function expectRed(spoil, line) {
  const { code, out } = oracle(spoil);
  expect(out).toContain(line);
  expect(code).toBe(1);
}

/** Порча — строка `text` в место `place`; оракул называет файл этого места и причину. */
function expectRedAt(place, text, reason) {
  const [file, put] = PLACES[place];
  expectRed((dist) => edit(dist, file, (old) => put(old, text)), `${file}: ${reason}`);
}

beforeAll(() => {
  clean = tempDir();
  writeFileSync(join(clean, 'brand.ts'), `export const SERVICE_NAME = '${NAME}';\n`);
  writeFileSync(join(clean, 'tokens.css'), TOKENS);
  for (const [path, text] of Object.entries(SAMPLE)) {
    mkdirSync(dirname(join(clean, 'dist', path)), { recursive: true });
    writeFileSync(join(clean, 'dist', path), text);
  }
});

afterAll(() => {
  for (const dir of dirs) rmSync(dir, { recursive: true, force: true });
});

describe('оракул сборки', () => {
  it('F5: чистая сборка-образец — код 0 и «проверено N файлов»', () => {
    const { code, out } = oracle();
    expect(out.trim()).toBe('проверено 7 файлов');
    expect(code).toBe(0);
  });

  it('F5: пустая или отсутствующая папка сборки — красный', () => {
    expectRed((dist) => rmSync(dist, { recursive: true }), 'dist: папки сборки нет');
    const empty = (dist) => {
      rmSync(dist, { recursive: true });
      mkdirSync(dist);
    };
    expectRed(empty, 'dist: папка сборки пуста');
  });

  it.each([JS, VENDOR, CSS])('F5: удалён файл, на который ссылается страница, — %s', (path) => {
    const line = `index.html: ссылка /${path}: такого файла в сборке нет`;
    expectRed((dist) => rmSync(join(dist, path)), line);
  });

  it('F5: файл без хеша в имени — красный', () => {
    const spoil = (dist) => {
      renameSync(join(dist, CSS), join(dist, 'assets/index.css'));
      edit(dist, 'index.html', (html) => html.replace(CSS, 'assets/index.css'));
    };
    expectRed(spoil, 'index.html: ссылка /assets/index.css: в имени файла нет хеша');
  });

  it('F5: лишний .map — красный', () => {
    const spoil = (dist) => writeFileSync(join(dist, `${JS}.map`), '{}');
    expectRed(spoil, `${JS}.map: карта исходников в сборке`);
  });

  it('F5: чужой заголовок — красный', () => {
    const spoil = (dist) => edit(dist, 'index.html', (html) => html.replace(NAME, 'Vite + React'));
    expectRed(spoil, `index.html: заголовок «Vite + React», а имя сервиса — «${NAME}»`);
  });

  it('F5: нет lang="ru" — красный', () => {
    const spoil = (dist) => edit(dist, 'index.html', (html) => html.replace('"ru"', '"en"'));
    expectRed(spoil, 'index.html: нет lang="ru"');
  });

  it.each([
    ['localhost в JS', 'js', 'fetch("http://localhost:8107/api");', 'localhost'],
    ['127.0.0.1 в CSS', 'css', '.a{background:url(http://127.0.0.1:5177/a.png)}', '127.0.0.1'],
    ['localhost в HTML', 'head', '<!-- http://localhost:5177/ -->', 'localhost'],
  ])('F5: адрес разработки — %s', (_form, place, text, address) => {
    expectRedAt(place, text, `адрес разработки ${address}`);
  });

  it.each([
    ['со схемой', 'head', '<link rel="stylesheet" href="https://cdn.example.com/x.css">'],
    ['без схемы', 'head', '<link rel="stylesheet" href="//cdn.example.com/x.css">'],
    ['в @import', 'css', '@import url("https://cdn.example.com/a.css");'],
    ['в preconnect', 'head', '<link rel="preconnect" href="https://cdn.example.com">'],
    ['в dns-prefetch', 'head', '<link rel="dns-prefetch" href="//cdn.example.com">'],
    ['в srcset', 'body', '<img srcset="/a.png 1x, https://cdn.example.com/b.png 2x" alt="">'],
    ['в url()', 'css', '.bg{background:url(//cdn.example.com/bg.png)}'],
  ])('F5: чужой узел в HTML и CSS — %s', (_form, place, text) => {
    expectRedAt(place, text, 'чужой узел cdn.example.com');
  });

  it('F5: новый узел в JS — красный, оракул называет узел и файл', () => {
    const text = 'fetch("https://api.example.org/v1");';
    expectRedAt('js', text, 'узел api.example.org не из списка узлов JS');
  });

  it.each([
    ['классический', '<script>window.x = 1;</script>'],
    ['модуль', '<script type="module">import "/assets/x.js";</script>'],
  ])('F7: встроенный скрипт (%s) — красный', (_kind, text) => {
    const [file, put] = PLACES.head;
    const { code, out } = oracle((dist) => edit(dist, file, (old) => put(old, text)));
    expect(out).toContain(`${file}: встроенных скриптов: 1`);
    expect(code).toBe(1);
  });

  // Для политики содержимого обработчик в разметке и адрес javascript: — тоже встроенный
  // скрипт: в разработке страница работает, в бою под политикой — пустая.
  it.each([
    [
      'обработчик у <body>',
      (html) => html.replace('<body>', '<body onload="x()">'),
      'onload у <body>',
    ],
    [
      'обработчик у <img>',
      (html) => html.replace('</body>', '<img src="/a.png" ONERROR="x()"></body>'),
      'onerror у <img>',
    ],
    [
      'адрес javascript:',
      (html) => html.replace('</body>', '<a href=" javascript:x()">a</a></body>'),
      'javascript: у <a href>',
    ],
  ])('F7: встроенный скрипт в разметке (%s) — красный', (_kind, change, reason) => {
    const { code, out } = oracle((dist) => edit(dist, 'index.html', change));
    expect(out).toContain(`index.html: встроенный скрипт — ${reason}`);
    expect(code).toBe(1);
  });

  // Без фона и текста схем в CSS сборки первая отрисовка до запуска приложения — цветами
  // библиотеки: переменные темы Mantine появляются только после запуска React.
  it.each([
    [
      'правило страницы убрано',
      (css) => css.replace(PAGE, ''),
      'dist: в CSS сборки нет правила страницы — фон var(--audit-bg), текст var(--audit-ink)',
    ],
    [
      'блок тёмной схемы убран',
      (css) => css.replace(DARK, ''),
      'dist: в CSS сборки нет --audit-bg схемы dark из токенов: oklch(17% 0.012 255)',
    ],
    [
      'текст тёмной схемы не из токенов',
      (css) => css.replace('oklch(95% .006 250)', 'oklch(90% .006 250)'),
      'dist: в CSS сборки нет --audit-ink схемы dark из токенов: oklch(95% 0.006 250)',
    ],
  ])('F12: фон и цвет текста обеих схем в CSS сборки — %s — красный', (_kind, change, line) => {
    expectRed((dist) => edit(dist, CSS, change), line);
  });

  // Шрифт — только латиница и кириллица (F6): лишний набор — лишний вес страницы и знаки, которых
  // правила вида не проверяли; без одного из двух страница уходит в запасной шрифт.
  const GREEK = 'assets/geologica-greek-wght-normal-Gk3Mn8Pq.woff2';
  const TTF = 'assets/geologica-latin-Lt4Xa9Qe.ttf';
  it.each([
    [
      'подложен файл набора греческого',
      (dist) => writeFileSync(join(dist, GREEK), 'wOF2'),
      `${GREEK}: шрифт не из наборов латиницы и кириллицы`,
    ],
    [
      'подложен шрифт другого вида',
      (dist) => writeFileSync(join(dist, TTF), 'ttf'),
      `${TTF}: шрифт не из наборов латиницы и кириллицы`,
    ],
    [
      'файла кириллицы нет',
      (dist) => rmSync(join(dist, CYRILLIC)),
      'dist: нет файла шрифта с кириллицей',
    ],
    [
      'файла латиницы нет',
      (dist) => rmSync(join(dist, LATIN)),
      'dist: нет файла шрифта с латиницей',
    ],
  ])('F6: шрифт в сборке — %s — красный', (_kind, spoil, line) => {
    expectRed(spoil, line);
  });

  it('F6: шрифт с сервера шрифтов в CSS сборки — красный', () => {
    const face = '@font-face{src:url(https://fonts.gstatic.com/s/geologica/a.woff2)}';
    expectRedAt('css', face, 'чужой узел fonts.gstatic.com');
  });
});
