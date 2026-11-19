// Контраст цветов темы по формуле WCAG — только для тестов (F11). Цвет — OKLCH из файла токенов:
// OKLCH → OKLab → LMS → линейный sRGB, и уже из линейных каналов — относительная яркость.

const OKLCH = /^oklch\(\s*([\d.]+)%\s+([\d.]+)\s+([\d.]+)\s*\)$/;
// Запас на округление записи: токен с тремя знаками после точки выходит за край на тысячные.
const GAMUT_SLACK = 0.002;

type Rgb = readonly [number, number, number];

/** Линейные r, g, b цвета OKLCH; у цвета вне охвата sRGB канал выходит за 0…1. */
export function linearRgb(color: string): Rgb {
  const match = OKLCH.exec(color);
  if (!match) throw new Error(`цвет не в записи oklch(L% C H): «${color}»`);
  const light = Number(match[1]) / 100;
  const chroma = Number(match[2]);
  const hue = (Number(match[3]) * Math.PI) / 180;
  const a = chroma * Math.cos(hue);
  const b = chroma * Math.sin(hue);
  const l = (light + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (light - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (light - 0.0894841775 * a - 1.291485548 * b) ** 3;
  return [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ];
}

export function inGamut(color: string): boolean {
  return linearRgb(color).every((channel) => channel >= -GAMUT_SLACK && channel <= 1 + GAMUT_SLACK);
}

/** Относительная яркость WCAG; канал за краем охвата берётся по краю. */
function luminance(color: string): number {
  const [r, g, b] = linearRgb(color).map((channel) => Math.min(1, Math.max(0, channel))) as [
    number,
    number,
    number,
  ];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** Контраст пары: от 1 (одинаковые) до 21 (чёрный на белом). */
export function contrast(one: string, two: string): number {
  const [x, y] = [luminance(one), luminance(two)];
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}
