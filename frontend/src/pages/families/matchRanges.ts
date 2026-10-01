const QUOTE_CHARS = new Set([..."«»“”„\"'‘’"]);

/** Диапазон `[from, to)` в исходной строке. */
export interface Range {
  from: number;
  to: number;
}

const WORD_CHAR = /[\p{L}\p{N}_]/u;

/**
 * Организационные формы, которые сервер снимает из имени при сборе словаря
 * (`_ORG_FORMS_CASEFOLD` в `services/semantic_privacy.py`), в нижнем регистре.
 * В видимой строке форма остаётся, в том числе посередине имени, — поиск
 * пропускает её между словами записи.
 */
const ORG_FORMS = ["ооо", "ао", "пао", "зао", "оао", "ип", "тоо", "llp", "гк", "сз"];

function escapeRegExp(word: string): string {
  return word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

const NON_WORD = "[^\\p{L}\\p{N}_]+";
const WORD_GAP = `${NON_WORD}(?:(?:${ORG_FORMS.join("|")})${NON_WORD})*`;

/** Запись словаря: имя (объект, подрядчик) или номер (договор, тендер) — буквально. */
export type Needle = string | { text: string; kind: string };

const LITERAL_KINDS = new Set(["contract", "tender"]);

/**
 * Шаблон записи. Имя: слова записи через любой не-словесный промежуток с
 * необязательными организационными формами (так ищет сервер). Номер: слова
 * через один пробел, без форм и знаков.
 */
function needlePattern(needle: string, literal: boolean): RegExp {
  const words = literal ? needle.split(" ") : (needle.match(/[\p{L}\p{N}_]+/gu) ?? []);
  const body = words.map(escapeRegExp).join(literal ? " " : WORD_GAP);
  return new RegExp(body, "gu");
}

/** Позиция `index` не буква и не цифра (или вне строки) — граница слова. */
function isBoundary(text: string, index: number): boolean {
  return index < 0 || index >= text.length || !WORD_CHAR.test(text[index]);
}

/**
 * Диапазоны совпадений в видимой строке. Слова словаря хранятся в нормальной
 * форме проверки (`services/semantic_privacy.py`): кавычки заменены пробелом,
 * пробелы схлопнуты, регистр снят. Видимая строка приводится к той же форме
 * посимвольно, с картой обратно в исходные позиции — так подсвечивается то,
 * что человек видит («Ромашка» в кавычках), а не нормализованная копия.
 */
export function findMatchRanges(text: string, needles: readonly Needle[]): Range[] {
  let normalized = "";
  const starts: number[] = [];
  const ends: number[] = [];
  let pos = 0;
  for (const ch of text) {
    const from = pos;
    pos += ch.length;
    const isSpace = QUOTE_CHARS.has(ch) || /\s/u.test(ch);
    const piece = isSpace ? " " : ch.toLowerCase();
    if (isSpace && normalized.endsWith(" ")) {
      ends[ends.length - 1] = pos;
      continue;
    }
    for (let i = 0; i < piece.length; i++) {
      normalized += piece[i];
      starts.push(from);
      ends.push(pos);
    }
  }
  const found: Range[] = [];
  for (const entry of needles) {
    const needle = (typeof entry === "string" ? entry : entry.text).trim();
    if (needle === "") continue;
    const literal = typeof entry !== "string" && LITERAL_KINDS.has(entry.kind);
    const pattern = needlePattern(needle, literal);
    if (pattern.source === "(?:)") continue;
    let hit = pattern.exec(normalized);
    while (hit !== null) {
      const at = hit.index;
      const end = at + hit[0].length;
      // Сервер ищет по границам слов: «ромашка» внутри «ромашкам» не совпадение.
      if (isBoundary(normalized, at - 1) && isBoundary(normalized, end)) {
        found.push({ from: starts[at], to: ends[end - 1] });
      }
      // Следующий поиск — с символа после начала: записи могут перекрываться.
      pattern.lastIndex = at + 1;
      hit = pattern.exec(normalized);
    }
  }
  found.sort((a, b) => a.from - b.from || b.to - a.to);
  const merged: Range[] = [];
  for (const range of found) {
    const last = merged.at(-1);
    if (last && range.from <= last.to) last.to = Math.max(last.to, range.to);
    else merged.push({ ...range });
  }
  return merged;
}
