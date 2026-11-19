// Оракул сборки фронта (F5, F7): страница не ссылается на несуществующее, в сборке нет карт,
// адресов разработки, чужих узлов и встроенных скриптов — CSP для скриптов обойдётся без хешей.
// Запуск из `frontend/`: node scripts/check-dist.mjs [папка сборки = dist] [файл имени =
// src/brand.ts]. Без зависимостей: оракул работает и тогда, когда сломаны пакеты.
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
const ATTR = /([^\s"'=<>/]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g;
const CODE = new Set(['.html', '.js', '.mjs', '.css']);

const [dist = 'dist', brand = 'src/brand.ts'] = process.argv.slice(2);
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
  const links = tags(html, 'link').map((attrs) => attrs.get('href'));
  for (const ref of [...scripts.map((attrs) => attrs.get('src')), ...links].filter(Boolean)) {
    const path = ref.replace(/[?#].*$/, '').replace(/^\.?\//, '');
    if (!files.includes(path)) say(page, `ссылка ${ref}: такого файла в сборке нет`);
    else if (!HASHED.test(path)) say(page, `ссылка ${ref}: в имени файла нет хеша`);
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
}

if (problems.length > 0) {
  for (const line of problems) console.error(line);
  console.error(`оракул сборки: красный, бед — ${problems.length}`);
  process.exitCode = 1;
} else {
  console.log(`проверено ${files.length} ${filesWord(files.length)}`);
}
