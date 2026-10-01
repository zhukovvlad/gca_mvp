const QUOTE_CHARS = new Set([..."«»“”„\"'‘’"]);

/** Диапазон `[from, to)` в исходной строке. */
export interface Range {
  from: number;
  to: number;
}

const ALNUM = /[\p{L}\p{N}]/u;
const DIGIT = /\p{Nd}/u;
/** Слово имени — прогон букв или прогон цифр (как `_WORD_RUN_RE` на сервере). */
const WORD_RUN = /[\p{L}\p{No}\p{Nl}]+|\p{Nd}+/gu;

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

const NON_WORD = "[^\\p{L}\\p{N}]+";
const WORD_GAP = `${NON_WORD}(?:(?:${ORG_FORMS.join("|")})${NON_WORD})*`;

/** Запись словаря: имя (объект, подрядчик) или номер (договор, тендер) — буквально. */
export type Needle = string | { text: string; kind: string };

const LITERAL_KINDS = new Set(["contract", "tender"]);

/**
 * Шаблон записи. Имя: слова записи (прогоны букв и цифр) через любой промежуток
 * из не-букв и не-цифр с необязательными организационными формами (так ищет
 * сервер). Номер: слова через один пробел, без форм и знаков.
 */
function needlePattern(needle: string, literal: boolean): RegExp {
  const words = literal ? needle.split(" ") : (needle.match(WORD_RUN) ?? []);
  const body = words.map(escapeRegExp).join(literal ? " " : WORD_GAP);
  return new RegExp(body, "gu");
}

/** Позиция `index` не буква и не цифра (или вне строки) — граница слова. */
function isBoundary(text: string, index: number): boolean {
  if (index < 0 || index >= text.length) return true;
  const unit = text.charCodeAt(index);
  const isLowSurrogate = unit >= 0xdc00 && unit <= 0xdfff;
  const from = isLowSurrogate && index > 0 ? index - 1 : index;
  return !ALNUM.test(String.fromCodePoint(text.codePointAt(from) ?? unit));
}

/** Нормальная форма видимой строки с картой обратно в исходные позиции. */
interface Normalized {
  text: string;
  starts: number[];
  ends: number[];
}

/**
 * Кавычки и пробелы схлопываются в один пробел, регистр снимается. С
 * `splitLetterDigit` на границе буква–цифра (`ЖК1`) вставляется пробел без
 * позиции в исходной строке: имя ищется по прогонам букв и цифр, номер — буквально.
 */
function normalize(text: string, splitLetterDigit: boolean): Normalized {
  let normalized = "";
  const starts: number[] = [];
  const ends: number[] = [];
  let pos = 0;
  let prev = "";
  for (const ch of text) {
    const from = pos;
    pos += ch.length;
    const isSpace = QUOTE_CHARS.has(ch) || /\s/u.test(ch);
    const piece = isSpace ? " " : ch.toLowerCase();
    if (isSpace && normalized.endsWith(" ")) {
      ends[ends.length - 1] = pos;
      continue;
    }
    if (splitLetterDigit && prev !== "") {
      const next = String.fromCodePoint(piece.codePointAt(0) ?? 0x20);
      if (ALNUM.test(prev) && ALNUM.test(next) && DIGIT.test(prev) !== DIGIT.test(next)) {
        normalized += " ";
        starts.push(from);
        ends.push(from);
      }
    }
    for (let i = 0; i < piece.length; i++) {
      normalized += piece[i];
      starts.push(from);
      ends.push(pos);
    }
    prev = Array.from(piece).at(-1) ?? " ";
  }
  return { text: normalized, starts, ends };
}

/**
 * Диапазоны совпадений в видимой строке. Слова словаря хранятся в нормальной
 * форме проверки (`services/semantic_privacy.py`): кавычки заменены пробелом,
 * пробелы схлопнуты, регистр снят. Видимая строка приводится к той же форме
 * посимвольно, с картой обратно в исходные позиции — так подсвечивается то,
 * что человек видит («Ромашка» в кавычках), а не нормализованная копия.
 */
export function findMatchRanges(text: string, needles: readonly Needle[]): Range[] {
  const asName = normalize(text, true);
  const asNumber = normalize(text, false);
  const found: Range[] = [];
  for (const entry of needles) {
    const needle = (typeof entry === "string" ? entry : entry.text).trim();
    if (needle === "") continue;
    const literal = typeof entry !== "string" && LITERAL_KINDS.has(entry.kind);
    const pattern = needlePattern(needle, literal);
    if (pattern.source === "(?:)") continue;
    const { text: normalized, starts, ends } = literal ? asNumber : asName;
    let hit = pattern.exec(normalized);
    while (hit !== null) {
      const at = hit.index;
      const end = at + hit[0].length;
      // Сервер ищет по границам слов: «ромашка» внутри «ромашкам» не совпадение.
      if (isBoundary(normalized, at - 1) && isBoundary(normalized, end)) {
        found.push({ from: starts[at], to: ends[end - 1] });
      }
      // Следующий поиск — с символа после начала: записи могут перекрываться.
      pattern.lastIndex = at + ((normalized.codePointAt(at) ?? 0) > 0xffff ? 2 : 1);
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
