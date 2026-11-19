// Токены живут в tokens.css. Тема ссылается на них по имени переменной — значение подставляет
// браузер; тесты читают файл и разбирают его здесь — разборщик один. Текста файла в коде нет:
// `?raw` у CSS Vitest отдаёт пустой строкой, и тема в тестах осталась бы без токенов.

export const SCHEMES = ['light', 'dark'] as const;
export type Scheme = (typeof SCHEMES)[number];
type Block = Record<string, string>;

const PREFIX = '--audit-';

/** Ссылка на токен для темы: роль `onPrimary` → `var(--audit-on-primary)`. */
export function cssVar(role: string): string {
  return `var(${PREFIX}${role.replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`)})`;
}

function declarations(body: string): Block {
  const block: Block = {};
  for (const line of body.split(';')) {
    const at = line.indexOf(':');
    if (at > 0) block[line.slice(0, at).trim()] = line.slice(at + 1).trim();
  }
  return block;
}

/** Блоки файла: заголовок (`:root`, `@media … :root`) → свойства. Комментарии не в счёт. */
export function parseBlocks(text: string): Map<string, Block> {
  const blocks = new Map<string, Block>();
  const heads: string[] = [];
  let chunk = '';
  for (const char of text.replace(/\/\*[\s\S]*?\*\//g, '')) {
    if (char === '{') heads.push(chunk.trim().replace(/\s+/g, ' '));
    else if (char === '}') {
      const props = declarations(chunk);
      if (Object.keys(props).length > 0) blocks.set(heads.join(' '), props);
      heads.pop();
    } else {
      chunk += char;
      continue;
    }
    chunk = '';
  }
  return blocks;
}

/** Цвета схемы — роль → значение; роль по имени переменной: `--audit-on-primary` → `onPrimary`. */
export function schemeColors(blocks: Map<string, Block>, scheme: Scheme): Block {
  const block = blocks.get(`:root[data-mantine-color-scheme='${scheme}']`) ?? {};
  return Object.fromEntries(
    Object.entries(block)
      .filter(([name]) => name.startsWith(PREFIX))
      .map(([name, value]) => [
        name.slice(PREFIX.length).replace(/-([a-z])/g, (_, letter: string) => letter.toUpperCase()),
        value,
      ]),
  );
}
