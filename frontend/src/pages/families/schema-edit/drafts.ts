import type { FamilySchema, FamilySchemaParameter, SchemaEditDraft, SchemaRange } from "@/types/domain";

/**
 * Строка окна правки: черновик параметра и ключ, по которому его находят список и редактор.
 * Новый параметр номера не имеет (`ordinal: null`) — ключ ему выдаёт окно.
 */
export interface EditorRow {
  key: string;
  draft: SchemaEditDraft;
}

/** Нормализация текста значения, как у списка значений на сервере: пробелы по краям и регистр не различают. */
export function normText(text: string): string {
  return text.trim().toLowerCase().replace(/\s+/g, " ");
}

/** Живые значения параметра: слитые — синонимы своей цели и в список правки не входят. */
export function liveValueTexts(parameter: FamilySchemaParameter): string[] {
  return parameter.values.filter((value) => value.merged_into_id === null).map((value) => value.value);
}

/** Черновик параметра по сохранённому: тело правки без изменений. У диапазонов `values` пуст — подписи строит сервер. */
export function draftOf(parameter: FamilySchemaParameter): SchemaEditDraft {
  const isRange = parameter.value_kind === "range";
  return {
    ordinal: parameter.ordinal,
    name: parameter.name,
    role: parameter.role,
    value_kind: parameter.value_kind,
    closed: isRange ? true : parameter.closed,
    values: isRange ? [] : liveValueTexts(parameter),
    range: isRange && parameter.range !== null ? { ...parameter.range } : null,
  };
}

export function rowsOf(schema: FamilySchema): EditorRow[] {
  return schema.parameters.map((parameter) => ({
    key: `p${parameter.ordinal}`,
    draft: draftOf(parameter),
  }));
}

/** Тело запроса: границы с десятичной запятой читаются как числа, у точного `range` не уходит. */
export function wireDrafts(drafts: SchemaEditDraft[]): SchemaEditDraft[] {
  return drafts.map((draft) => ({
    ...draft,
    range:
      draft.value_kind === "range" && draft.range !== null
        ? {
            start: draft.range.start.trim().replace(",", "."),
            stop: draft.range.stop.trim().replace(",", "."),
            step: draft.range.step.trim().replace(",", "."),
          }
        : null,
    values: draft.value_kind === "range" ? [] : draft.values,
  }));
}

export function sameRange(a: SchemaRange | null, b: SchemaRange | null): boolean {
  if (a === null || b === null) return a === b;
  return a.start === b.start && a.stop === b.stop && a.step === b.step;
}

const SCALE = 1_000_000;

function scaled(text: string): number | null {
  const trimmed = text.trim().replace(",", ".");
  if (!/^-?\d+(\.\d{1,6})?$/.test(trimmed)) return null;
  return Math.round(Number(trimmed) * SCALE);
}

/**
 * Сколько внутренних диапазонов даст `{start, stop, step}` (кроме двух крайних); `null` —
 * границы не числа, `start >= stop`, шаг не положителен или число шагов не целое. Сервер
 * проверяет то же и остаётся последним словом: здесь только подсказка под полями.
 */
export function rangeInnerCount(range: SchemaRange): number | null {
  const start = scaled(range.start);
  const stop = scaled(range.stop);
  const step = scaled(range.step);
  if (start === null || stop === null || step === null) return null;
  if (start >= stop || step <= 0) return null;
  const span = stop - start;
  if (span % step !== 0) return null;
  return span / step;
}

/** Сумма строк каталога по значениям параметра (числа сервера). */
export function rowsTotal(parameter: FamilySchemaParameter): number {
  return parameter.values.reduce((sum, value) => sum + value.rows, 0);
}
