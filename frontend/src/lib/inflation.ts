import { addDecimalStrings, multiplyDecimalStrings, normalizeDecimalInput } from "@/lib/decimal";
import { roundDecimalPercent } from "@/lib/format";

/**
 * Расшифровка коэффициента инфляции в УРОВЕНЬ (спека 2026-08-18 §2.4).
 *
 * **Это защита, а не украшение, и она стоит тринадцати ошибок разом.** Подпись у
 * поля называет коэффициент («1.083 означает рост на 8,3 %»), но «прирост за год»
 * человек введёт как `0.083` — схема примет его, потому что условие только
 * `coefficient > 0`, и приведение посчитает дефляцию на 91,7 % молча и сразу на
 * всех сравнениях. Расшифровка рядом с полем и есть то, что делает ошибку видимой
 * ДО сохранения.
 *
 * Считается ТОЧНОЙ арифметикой строк: `k − 1`, затем `× 100`, затем округление до
 * одного знака. `Number()` здесь запрещён по той же причине, по которой он запрещён
 * в деньгах (§3 AGENTS.md) — коэффициент участвует в расчёте, который защищают
 * перед банком.
 */
export interface CoefficientLevel {
  /** Уровень со знаком: «+8,3%». Пусто, если вход не разобран. */
  level: string;
  /** Человеческий текст: «Рост 8,3 %», «Снижение 91,7 %», «—». */
  text: string;
  tone: "ok" | "bad" | "muted";
}

const EMPTY: CoefficientLevel = { level: "", text: "—", tone: "muted" };

/**
 * Границы «невероятного» уровня. Красным помечается НЕ снижение как таковое —
 * снижение цен законно, — а уровень, которого в годовом индексе не бывает: ниже
 * −50 % либо выше +100 %. Именно в эту область попадает ошибка ×13 (`0.083` →
 * −91,7 %), и именно поэтому порог стоит здесь, а не в схеме запроса: искусственный
 * диапазон допустимых значений спека вводить запрещает (§2.4), он отверг бы законный
 * год высокой инфляции.
 */
const IMPLAUSIBLE_BELOW = -50;
const IMPLAUSIBLE_ABOVE = 100;

export function coefficientLevel(raw: string): CoefficientLevel {
  const normalized = normalizeDecimalInput(raw ?? "");
  if (!normalized) return EMPTY;

  const delta = addDecimalStrings(normalized, "-1");
  if (delta === null) return EMPTY;

  const percent = multiplyDecimalStrings(delta, "100");
  if (percent === null) return EMPTY;

  const rounded = roundDecimalPercent(percent, 1);
  if (rounded === null) return EMPTY;

  // `roundDecimalPercent` на неразобранном входе отдаёт сам вход со знаком 0 —
  // такой текст в расшифровке был бы мусором, а не уровнем.
  if (rounded.sign === 0 && !rounded.text.endsWith("%")) return EMPTY;

  const magnitude = rounded.text.replace(/^[+-]/, "").replace("%", "");
  const text =
    rounded.sign === 0
      ? "Без изменения"
      : rounded.sign > 0
        ? `Рост ${magnitude} %`
        : `Снижение ${magnitude} %`;

  return { level: rounded.text, text, tone: toneOf(percent, rounded.sign) };
}

function toneOf(percent: string, sign: number): CoefficientLevel["tone"] {
  if (sign === 0) return "muted";
  // Сравнение с порогом — единственное место, где величина уходит в `number`, и
  // это безопасно: сравнивается ПОРЯДОК, а не считается сумма. Само число уже
  // посчитано точно выше и в результат идёт строкой.
  const value = Number(percent);
  if (!Number.isFinite(value)) return "muted";
  return value < IMPLAUSIBLE_BELOW || value > IMPLAUSIBLE_ABOVE ? "bad" : "ok";
}
