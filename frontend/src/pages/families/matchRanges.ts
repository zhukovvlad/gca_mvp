const QUOTE_CHARS = new Set([..."«»“”„\"'‘’"]);

/** Диапазон `[from, to)` в исходной строке. */
export interface Range {
  from: number;
  to: number;
}

const WORD_CHAR = /[\p{L}\p{N}_]/u;

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
export function findMatchRanges(text: string, needles: readonly string[]): Range[] {
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
  for (const raw of needles) {
    const needle = raw.trim();
    if (needle === "") continue;
    let at = normalized.indexOf(needle);
    while (at !== -1) {
      // Сервер ищет по границам слов: «ромашка» внутри «ромашкам» не совпадение.
      if (isBoundary(normalized, at - 1) && isBoundary(normalized, at + needle.length)) {
        found.push({ from: starts[at], to: ends[at + needle.length - 1] });
      }
      at = normalized.indexOf(needle, at + 1);
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
