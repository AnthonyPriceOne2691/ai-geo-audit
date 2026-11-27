// Образцы файла договора `contracts/api-refusals.json` для тестов [договор]: тест клиента идёт по
// всем образцам, тест заглушки берёт нужные по имени; ответ тестового сервера — телом образца.
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

export interface Sample {
  id: string;
  status: number;
  headers: Record<string, string>;
  body: { detail?: string; kind?: string; retry?: boolean; status?: string };
}

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
const FILE = JSON.parse(readFileSync(join(ROOT, 'contracts', 'api-refusals.json'), 'utf8')) as {
  samples: Sample[];
  proxy: Sample[];
};

/** Все образцы файла: ответы сервиса и раздел `proxy`. */
export const SAMPLES: readonly Sample[] = [...FILE.samples, ...FILE.proxy];

/** Образец по имени; нет такого в файле — красное с именем. */
export function sample(id: string): Sample {
  const found = SAMPLES.find((s) => s.id === id);
  if (!found) throw new Error(`в файле договора нет образца ${id}`);
  return found;
}

/** Ответ тестового сервера телом, кодом и заголовками образца. */
export const reply = (s: Sample) => (): Response =>
  new Response(JSON.stringify(s.body), { status: s.status, headers: s.headers });
