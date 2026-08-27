import type { CellState, ChangeKind, StageSummaryErrorCode } from "@/types/domain";

/**
 * Словари подписей страницы свода по этапам (спека 2026-08-27-stage-summary-design.md
 * §2.16, задача 8 плана). Вынесены из компонентов в отдельный модуль: файл,
 * экспортирующий React-компонент, не может экспортировать что-то ещё без потери
 * fast refresh (`react-refresh/only-export-components`, включён для страниц —
 * `frontend/eslint.config.js` выключает его только для `src/components/ui/**` и
 * `src/test/**`).
 */

/** Подпись состояния ячейки — все состояния, КРОМЕ «есть сумма» (у неё подписи нет,
 *  просто печатается число). */
export const STATE_LABEL: Record<Exclude<CellState, "amount">, string> = {
  removed: "снято",
  not_evaluated: "не оценивалась",
  absent: "—",
};

/** Подпись структурного вида изменения — без «процент», «только сумма» и «нет
 *  изменения»: у первых двух своя печать числа, у третьего печатать нечего. */
export const KIND_LABEL: Record<Exclude<ChangeKind, "percent" | "abs_only" | "none">, string> = {
  appeared: "появилась",
  reappeared: "вернулась",
  removed: "снято",
  disappeared: "нет в файле",
};

/** Подпись причины, по которой сумма/сравнение/сходимость недоступны — коды из
 *  контракта сервера (`backend/services/stage_summary.py`), не выдуманные. */
export const REASON_LABEL: Record<string, string> = {
  first_column: "первая колонка",
  unknown_vat_base: "база НДС неизвестна — сравнивать нечем",
  no_amounts: "суммы нет ни на одном конце",
  unallocated: "дельта «Нераспределённого» не читается как уступка",
  absent_endpoint: "статьи нет в файле на одном из концов",
  file_total_unavailable: "итог файла не единогласен",
  non_positive_total: "итог одного из этапов неположителен — столбики не строятся",
  no_comparable_totals: "ни у одного этапа нет сопоставимого итога",
};

/** Подпись налоговой оси (AGENTS.md §10) — печатается на поверхности всегда,
 *  а не только в подсказке. */
export const TAX_LABEL = {
  gross: (rate: string) => `Все суммы — с НДС ${rate} %, ставка одна во всех выбранных этапах`,
  net: (rates: string) => `Все суммы — без НДС: ставки этапов расходятся (${rates})`,
  none: "Сопоставимых сумм нет: база НДС неизвестна у всех выбранных этапов",
};

/** Подпись ценового уровня — вторая ось шапки, рядом с налоговой (AGENTS.md §10). */
export const NOMINAL_PRICE_CAPTION = "Цены номинальные, без приведения к ценовому уровню месяца";

/** Подпись отказа свода — коды из {@link StageSummaryErrorCode}, исчерпывающе. */
export const ERROR_LABEL: Record<StageSummaryErrorCode, string> = {
  tender_not_found: "Тендер не найден",
  offer_not_found: "Предложение не найдено в этом тендере",
  too_few_offers: "Для свода нужны хотя бы два этапа",
  one_offer_per_round: "В одном раунде — одно предложение",
  single_participant:
    "Выбранные предложения принадлежат разным участникам — свод строится по одному участнику",
  offer_has_no_estimate: "У предложения нет сметы: раунд был заменён другим файлом",
};
