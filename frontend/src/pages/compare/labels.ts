import type { ComparisonBucket, ComparisonIncompleteReason, ComparisonVatMode } from "@/types/domain";

/**
 * Подписи экрана сравнения — ОДИН словарь на экран, в отдельном модуле.
 *
 * Отдельным файлом, а не константами в `ComparePage.tsx`, по той же причине и
 * тем же прецедентом, что `deviationTone.ts` рядом: `react-refresh/only-export-
 * components` не пускает файл компонента экспортировать что-либо кроме
 * компонентов, и сам же называет верное лечение — «use a new file to share
 * constants between components» (замерено: экспорт константы из `ComparePage.tsx`
 * даёт `error`, а не предупреждение).
 *
 * Почему это важнее удобства: таблица сравнения и диаграмма стоимости обязаны
 * подписывать причины неполноты ОДНИМИ словами (спека диаграммы стоимости §2.9).
 * Прежняя редакция передавала словарь диаграмме ПРОПОМ — и тогда «один словарь
 * на экран» держался на том, что все вызывающие передают один и тот же объект;
 * тест диаграммы уже передавал свой собственный. Общий модуль делает
 * утверждение верным по построению, а не по дисциплине.
 */
export const BUCKET_LABELS: Record<ComparisonBucket, string> = {
  base: "ДГП",
  amendments: "ДС",
  total: "Итого",
};

export const VAT_MODE_LABELS: Record<ComparisonVatMode, string> = {
  own: "Своя ставка",
  single: "Единая",
  net: "Без НДС",
};

/**
 * Словарь причин неполноты (спека сравнения §2.1.3) — тот же смысл, что
 * паспортная подпись, но список СОВМЕЩАЕТ все причины разом, а не выбирает
 * старшую.
 */
export const REASON_LABELS: Record<ComparisonIncompleteReason, string> = {
  unpriced_rows: "без цены",
  not_finite_rows: "с ошибкой",
  vat_base_unknown: "неизвестна база НДС",
  display_rate_undefined: "ставка показа не определена",
};
