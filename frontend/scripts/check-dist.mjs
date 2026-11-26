// Оракул сборки фронта (F5, F6, F7, F12): страница не ссылается на несуществующее, в сборке нет
// карт, адресов разработки, чужих узлов и встроенных скриптов — CSP для скриптов обойдётся без
// хешей; шрифт — только наборами латиницы и кириллицы; фон и цвет текста обеих схем — в CSS сборки;
// скрипт схемы — обычным тегом в <head>. Запуск из `frontend/`: node scripts/check-dist.mjs
// [папка сборки = dist] [файл имени = src/brand.ts] [файл токенов = src/theme/tokens.css].
// Без зависимостей: оракул работает и тогда, когда сломаны пакеты.
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { extname, join, sep } from 'node:path';

// Узлы, которые можно встретить в JS сборки, — каждый с причиной. Новый узел — словом человека.
const JS_HOSTS = new Map([
  ['www.w3.org', 'пространства имён SVG, MathML и XLink: это имена, по ним не ходят'],
  ['react.dev', 'ссылка на справку в текстах ошибок React'],
  ['mantine.dev', 'ссылка на справку в текстах ошибок Mantine'],
]);
// Узел в любой форме: со схемой и без неё (`//узел/x.css`), в атрибутах, `srcset`, `@import`
// и `url()` — после `//` всегда идёт имя узла.
const HOST = /\/\/((?:[a-z\d-]+\.)+[a-z\d-]+)/gi;
const DEV = /localhost|127\.0\.0\.1/gi;
// Имя, как у Vite: `assets/<имя>-<хеш из 8 знаков>.<расширение>` — без хеша кеш браузера
// отдаст старый файл после выката.
const HASHED = /^assets\/[^/]+-[\w-]{8}\.\w+$/;
// Скрипт схемы (F12) ставит схему до первой отрисовки: файлом из public/, имя постоянное.
const SCHEME_SCRIPT = 'color-scheme.js';
// Файлы без хеша в имени — поимённо, с причиной. Новый — словом человека.
const UNHASHED = new Map([
  [SCHEME_SCRIPT, 'скрипт схемы из public/: имя постоянное, свежесть — no-cache правилом web'],
]);
const ATTR = /([^\s"'=<>/]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g;
const CODE = new Set(['.html', '.js', '.mjs', '.css']);

// Блок цветов схемы у корня — как его ставит Mantine; минификатор снимает кавычки.
const SCHEME = /^(?::root)?\[data-mantine-color-scheme=['"]?(light|dark)['"]?\]$/;
const PAINT = ['--audit-bg', '--audit-ink'];
const PAGE_PAINT = [
  ['background-color', 'var(--audit-bg)'],
  ['color', 'var(--audit-ink)'],
];

const [dist = 'dist', brand = 'src/brand.ts', tokens = 'src/theme/tokens.css'] =
  process.argv.slice(2);
const page = join(dist, 'index.html');
const problems = [];
const say = (file, reason) => problems.push(`${file}: ${reason}`);

function read(path) {
  try {
    return readFileSync(path, 'utf8');
  } catch {
    return null;
  }
}

/** Файлы сборки путями от её корня через `/`; `null` — папки нет. */
function listFiles(root) {
  try {
    return readdirSync(root, { recursive: true, encoding: 'utf8' })
      .filter((rel) => statSync(join(root, rel)).isFile())
      .map((rel) => rel.split(sep).join('/'));
  } catch {
    return null;
  }
}

/** Атрибуты одного тега: имя атрибута → значение. */
function attrsOf(text) {
  const attrs = new Map();
  for (const [, key, ...values] of text.matchAll(ATTR)) {
    attrs.set(key.toLowerCase(), values.find((value) => value !== undefined) ?? '');
  }
  return attrs;
}

/** Атрибуты каждого тега `name`. */
function tags(html, name) {
  return [...html.matchAll(new RegExp(`<${name}\\b([^>]*)>`, 'gi'))].map(([, text]) =>
    attrsOf(text),
  );
}

/** Для политики содержимого обработчик `on…=` и адрес `javascript:` — тоже встроенный скрипт. */
function checkMarkup(html) {
  for (const [, tag, text] of html.matchAll(/<([a-z][\w-]*)\b([^>]*)>/gi)) {
    const where = `<${tag.toLowerCase()}`;
    for (const [key, value] of attrsOf(text)) {
      if (key.startsWith('on')) say(page, `встроенный скрипт — ${key} у ${where}>`);
      else if (/^\s*javascript:/i.test(value)) {
        say(page, `встроенный скрипт — javascript: у ${where} ${key}>`);
      }
    }
  }
}

const found = (text, re, group) =>
  new Set([...text.matchAll(re)].map((m) => m[group].toLowerCase()));

const pathOf = (ref) => ref.replace(/[?#].*$/, '').replace(/^\.?\//, '');

/** Скрипт схемы — обычным блокирующим тегом в <head> (F12): модуль, `defer` и `async` исполнятся
 *  после разбора страницы, и первая отрисовка выйдет светлой. */
function checkSchemeScript(html) {
  const ref = `/${SCHEME_SCRIPT}`;
  const isScheme = (attrs) => pathOf(attrs.get('src') ?? '') === SCHEME_SCRIPT;
  const head = /<head\b[^>]*>([\s\S]*?)<\/head>/i.exec(html)?.[1] ?? '';
  const inHead = tags(head, 'script').filter(isScheme);
  if (inHead.length === 0) {
    const elsewhere = tags(html, 'script').some(isScheme);
    return say(page, elsewhere ? `скрипт схемы ${ref} — не в <head>` : `нет скрипта схемы ${ref}`);
  }
  for (const attrs of inHead) {
    const late = ['defer', 'async'].filter((key) => attrs.has(key));
    if ((attrs.get('type') ?? '').toLowerCase() === 'module') late.push('type="module"');
    for (const what of late)
      say(page, `скрипт схемы ${ref} с ${what} — нужен обычный блокирующий тег`);
  }
}

/** Страница: язык, имя сервиса в заголовке, ссылки — на свои файлы с хешем, скрипты — файлами. */
function checkPage(files, name) {
  const html = read(page);
  if (html === null) return say(page, 'страницы нет');
  if (tags(html, 'html')[0]?.get('lang') !== 'ru') say(page, 'нет lang="ru"');
  const title = /<title>([^<]*)<\/title>/i.exec(html)?.[1]?.trim() ?? '';
  if (name !== undefined && title !== name) {
    say(page, `заголовок «${title}», а имя сервиса — «${name}»`);
  }
  const scripts = tags(html, 'script');
  const inline = scripts.filter((attrs) => !attrs.get('src')).length;
  if (inline > 0) say(page, `встроенных скриптов: ${inline} — у каждого <script> нужен src`);
  checkMarkup(html);
  checkSchemeScript(html);
  const links = tags(html, 'link').map((attrs) => attrs.get('href'));
  for (const ref of [...scripts.map((attrs) => attrs.get('src')), ...links].filter(Boolean)) {
    const path = pathOf(ref);
    if (!files.includes(path)) say(page, `ссылка ${ref}: такого файла в сборке нет`);
    else if (!HASHED.test(path) && !UNHASHED.has(path)) {
      say(page, `ссылка ${ref}: в имени файла нет хеша`);
    }
  }
}

/** Файл сборки: не карта; в коде нет адресов разработки и чужих узлов, в JS — узлы из списка. */
function checkFile(rel) {
  const file = join(dist, rel);
  if (rel.endsWith('.map')) return say(file, 'карта исходников в сборке');
  const ext = extname(rel);
  if (!CODE.has(ext)) return;
  const text = read(file) ?? '';
  for (const dev of found(text, DEV, 0)) say(file, `адрес разработки ${dev}`);
  const js = ext === '.js' || ext === '.mjs';
  for (const host of found(text, HOST, 1)) {
    if (!js) say(file, `чужой узел ${host}`);
    else if (!JS_HOSTS.has(host)) say(file, `узел ${host} не из списка узлов JS`);
  }
}

/** Свойства блока CSS; у блока с вложенными блоками (`@media`) — `null`. */
function propsOf(body) {
  if (body.includes('{')) return null;
  const at = (part) => part.indexOf(':');
  return new Map(body.split(';').map((p) => [p.slice(0, at(p)).trim(), p.slice(at(p) + 1).trim()]));
}

/** Блоки CSS верхнего уровня: [селектор, свойства]. */
function topBlocks(css) {
  const text = css.replace(/\/\*[\s\S]*?\*\//g, '');
  const blocks = [];
  let [depth, start, open] = [0, 0, 0];
  for (let at = 0; at < text.length; at += 1) {
    if (text[at] === '{' && depth++ === 0) open = at;
    if (text[at] === '}' && --depth === 0) {
      blocks.push([text.slice(start, open).trim(), propsOf(text.slice(open + 1, at))]);
      start = at + 1;
    }
  }
  return blocks;
}

/** Свойства блоков схемы у корня: схема → свойство → значение. */
function schemeProps(blocks) {
  const values = { light: new Map(), dark: new Map() };
  for (const [selector, props] of blocks) {
    const scheme = SCHEME.exec(selector)?.[1];
    if (scheme && props) for (const [key, value] of props) values[scheme].set(key, value);
  }
  return values;
}

// Минификатор пишет числа по-своему (`0.020` → `.02`) — они сравниваются значением.
const byValue = (text) => text.replace(/\d*\.?\d+/g, (n) => String(Number(n))).replace(/\s+/g, ' ');

/** Фон и цвет текста обеих схем стоят в CSS сборки до запуска приложения (F12): значения схем —
 *  из файла токенов, и правило страницы красит ими. */
function checkPaint(files) {
  const source = read(tokens);
  if (source === null) return say(tokens, 'файла токенов нет');
  const want = schemeProps(topBlocks(source));
  const css = files.filter((rel) => extname(rel) === '.css');
  const built = css.flatMap((rel) => topBlocks(read(join(dist, rel)) ?? ''));
  const have = schemeProps(built);
  for (const scheme of ['light', 'dark']) {
    for (const name of PAINT) {
      const value = want[scheme].get(name);
      if (value === undefined) say(tokens, `нет ${name} схемы ${scheme}`);
      else if (byValue(have[scheme].get(name) ?? '') !== byValue(value)) {
        say(dist, `в CSS сборки нет ${name} схемы ${scheme} из токенов: ${value}`);
      }
    }
  }
  const paints = ([selector, props]) =>
    /(^|[\s,>])body$/.test(selector) && PAGE_PAINT.every(([key, v]) => props?.get(key) === v);
  if (!built.some(paints)) {
    say(dist, 'в CSS сборки нет правила страницы — фон var(--audit-bg), текст var(--audit-ink)');
  }
}

// Шрифт (F6): файлы двух наборов знаков, как их называет пакет, с хешем Vite.
const FONT = /\.(woff2?|ttf|otf|eot)$/i;
const FONT_FILE = /^assets\/geologica-(latin|cyrillic)-wght-normal-[\w-]{8}\.woff2$/;
const FONT_SETS = new Map([
  ['latin', 'латиницей'],
  ['cyrillic', 'кириллицей'],
]);

/** Шрифт отдаётся только наборами латиницы и кириллицы, и оба набора на месте (F6). */
function checkFonts(files) {
  const sets = new Set();
  for (const rel of files.filter((file) => FONT.test(file))) {
    const set = FONT_FILE.exec(rel)?.[1];
    if (set) sets.add(set);
    else say(join(dist, rel), 'шрифт не из наборов латиницы и кириллицы');
  }
  for (const [set, word] of FONT_SETS) if (!sets.has(set)) say(dist, `нет файла шрифта с ${word}`);
}

/** «файл», «файла» или «файлов» — по числу. */
function filesWord(n) {
  const [ten, hundred] = [n % 10, n % 100];
  if (ten === 1 && hundred !== 11) return 'файл';
  if (ten >= 2 && ten <= 4 && (hundred < 12 || hundred > 14)) return 'файла';
  return 'файлов';
}

const name = /^export const SERVICE_NAME = (['"])(.*)\1;$/m.exec(read(brand) ?? '')?.[2];
if (name === undefined) say(brand, "нет строки export const SERVICE_NAME = '…';");
const files = listFiles(dist);
if (files === null) say(dist, 'папки сборки нет');
else if (files.length === 0) say(dist, 'папка сборки пуста');
else {
  checkPage(files, name);
  files.forEach(checkFile);
  checkPaint(files);
  checkFonts(files);
}

if (problems.length > 0) {
  for (const line of problems) console.error(line);
  console.error(`оракул сборки: красный, бед — ${problems.length}`);
  process.exitCode = 1;
} else {
  console.log(`проверено ${files.length} ${filesWord(files.length)}`);
}
