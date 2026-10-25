import { pluralRu } from "@/lib/format";
import type { Decimal, OurPriceLineState, OurPriceParticipant } from "@/types/domain";

import type { OfferFilter } from "./offerFilter";

/**
 * Тексты экранов «Против нашей цены» (спека 4а §2.9, §2.11, §2.13). Компоненты
 * текстов не сочиняют: подпись состояния, причины и вида цены берётся отсюда.
 */

/** Подпись состояния строки КП: одна на каждое значение закрытого множества. */
export const LINE_STATE_LABEL: Record<OurPriceLineState, string> = {
  in_progress: "каталог ещё разбирает",
  unknown_vat_base: "база НДС КП неизвестна",
  no_price: "в КП нет цены",
  no_area: "у объекта нет площади",
  no_variant: "вариант не определён",
  no_history: "нет наших договоров для сравнения",
  unit_mismatch: "разные единицы учёта",
  comparable: "сопоставима",
};

export const WAITING_ADMIN_LABEL = "ждёт решения администратора";

/** Почему у участника нет ₽/м² (поле `per_sqm_reason`). */
export const PER_SQM_REASON_LABEL: Record<"no_area" | "unknown_vat_base", string> = {
  no_area: "у объекта нет площади — ₽/м² не посчитать",
  unknown_vat_base: "база НДС КП неизвестна — сравнение недоступно",
};

export const NO_ESTIMATE_LABEL = "КП не загружено";

/** Причина пустой ячейки или карточки: `per_sqm_reason` либо состояние участника. */
export type EmptyReason = "no_estimate" | "no_area" | "unknown_vat_base";

export const EMPTY_REASON_LABEL: Record<EmptyReason, string> = {
  no_estimate: NO_ESTIMATE_LABEL,
  ...PER_SQM_REASON_LABEL,
};

/** Причина, по которой у участника нет ₽/м²: поле участника, а не ветка текста. */
export function emptyReasonOf(participant: OurPriceParticipant): EmptyReason | null {
  if (participant.state === "no_estimate") return "no_estimate";
  return participant.per_sqm_reason;
}

export const CHEAPEST_PER_SQM_LABEL = "дешевле всех на м²";
export const DETAIL_LINK_LABEL = "Разобрать по строкам →";

/** Ставка без хвостовых нулей: «22.00» → «22». Строка, не число. */
function rateText(rate: Decimal): string {
  return rate.includes(".") ? rate.replace(/\.?0+$/, "") : rate;
}

export const VAT_NET_LABEL = "без НДС";
export const VAT_UNKNOWN_LABEL = "база НДС неизвестна";
export const VAT_MIXED_RATES_LABEL = "без НДС — ставки участников разные";

export function vatShownLabel(rate: Decimal): string {
  return `с НДС ${rateText(rate)} %`;
}

export const VAT_SWITCH_LABEL = "С НДС по ставке КП";
export const INFLATION_SWITCH_LABEL = "Привести по ИПЦ";
export const INFLATION_SERIES_LABEL = "Ряд индексов";
export const NO_INFLATION_CAPTION = "цены без приведения";
export const RECALC_LABEL = "Пересчитываем цены…";

export const PRICES_CAPTION_NET = "цены без НДС";
export const PRICES_CAPTION_SHOWN_MIXED = "цены с НДС по ставке каждого КП";
export function pricesCaptionShown(rate: Decimal): string {
  return `цены ${vatShownLabel(rate)}`;
}

export const ARTICLES_TITLE = "Статьи × участники, ₽ на м² объекта";
export const ARTICLE_COLUMN_LABEL = "Статья";
export const FORK_COLUMN_LABEL = "Наши договоры класса, ₽/м²";
export const LEGEND_DEAR = "выше наших договоров";
export const LEGEND_CHEAP = "ниже наших договоров";
export const LEGEND_BEST = "подчёркнуто — дешевле всех участников по статье";
export const LEGEND_MISMATCH = "⚠ — строки в разных единицах";
export const MATRIX_HINT =
  "Под ценой — разница с нижней границей наших договоров класса × площадь объекта. Нажмите на ячейку — откроется разбор этого участника по строкам, сразу на этой статье.";

/** Значение `?article=` для строки «Нераспределённое» (у неё нет `work_category_id`). */
export const UNALLOCATED_ARTICLE = "unallocated";

export const CARD_LABEL = {
  bargain: "Можно выторговать",
  below: "Ниже всего, что платили",
  unitMismatch: "Разные единицы учёта",
  notComparable: "Не с чем сравнить",
} as const;

export function linesCount(n: number): string {
  return `${n} стр.`;
}

export function contractsWithContractor(n: number): string {
  return `у нас ${n} договор${pluralRu(n)} с ним`;
}

export function participantsCount(n: number): string {
  return `${n} участник${pluralRu(n)}`;
}

/** «2026-03-10» → «10.03.2026»: перестановкой частей строки, без часовых поясов. */
export function dateRu(iso: string): string {
  return iso.split("-").reverse().join(".");
}

export function medianCaption(pct: string): string {
  const text = pct.replace(".", ",");
  const signed = text.startsWith("-") || /^0(,0+)?$/.test(text) ? text : `+${text}`;
  return `${signed} % к медиане наших`;
}

export function progressCaption(done: number, total: number): string {
  return `Каталог ещё разбирает строки КП: разобрано ${done} из ${total}`;
}

export function waitingAdminCaption(n: number): string {
  return `${linesCount(n)} ${WAITING_ADMIN_LABEL}`;
}

const ERROR_NOT_FOUND = "Этап или тендер не найден";
const ERROR_GENERIC = "Не удалось построить экран «Против нашей цены»";
export const ERROR_TITLE = "Экран не построен";
export const BACK_LABEL = "К карточке тендера";

/**
 * Подпись отказа экрана. Код у 404 этапа и ряда не приходит, поэтому — по HTTP-статусу;
 * при выбранном ряде 404 печатает сообщение сервера: оно называет, чего именно нет.
 */
export function errorLabel(
  status: number | undefined,
  seriesSelected = false,
  serverMessage?: string
): string {
  if (status === 404 && seriesSelected && serverMessage) return serverMessage;
  return status === 404 ? ERROR_NOT_FOUND : ERROR_GENERIC;
}

export const REFUSAL_REASON_UNNAMED = "Причина не названа.";
export const PROGRESS_ARIA_LABEL = "Прогресс разбора каталога";
export const ARCHIVED_SUFFIX = " (в архиве)";
export function seriesFallbackLabel(raw: unknown): string {
  return `Ряд ${String(raw)}`;
}

export const BREADCRUMB_TENDERS = "Тендеры";
export const BREADCRUMB_OUR_PRICE = "Против нашей цены";

export function roundTitle(stageNo: number, participants: number): string {
  return `Этап ${stageNo} против нашей цены · ${participantsCount(participants)}`;
}

export function roundSubtitle(parts: {
  heldOn: string | null;
  objectTitle: string;
  area: Decimal | null;
  rateClass: string;
}): string {
  return [
    parts.heldOn !== null ? dateRu(parts.heldOn) : null,
    `объект ${parts.objectTitle}`,
    parts.area !== null ? `${parts.area} м²` : null,
    `класс ${parts.rateClass}`,
  ]
    .filter((part): part is string => part !== null)
    .join(" · ");
}

export const OFFER_SWITCHER_LABEL = "Участник:";

export function offerCrumbStage(stageNo: number): string {
  return `этап ${stageNo}: все участники`;
}

export function offerTitle(contractor: string): string {
  return `КП «${contractor}» против нашей цены`;
}

export const FILTER_LABEL: Record<OfferFilter, string> = {
  all: "Все строки",
  above_ours: "Дороже нашей расценки",
  below_paid: "Ниже всего, что платили",
  unit_mismatch: "Разные единицы учёта",
  not_comparable: "Не с чем сравнить",
};

export const FILTER_HINT: Record<Exclude<OfferFilter, "all">, string> = {
  above_ours: "потенциал торга",
  below_paid: "риск допработ и замены",
  unit_mismatch: "вопрос подрядчику про единицу учёта",
  not_comparable: "не разобраны, нет в договорах, нет цены",
};

export const PER_SQM_TILE_LABEL = "КП на м² объекта";

export const STRIP_TITLE = "Цена на м² объекта против наших договоров, ₽";
export const STRIP_LEGEND_CLASS = "тёмные — договоры класса объекта";
export const STRIP_LEGEND_OTHER = "светлые — другие классы";
export const STRIP_THIS_OFFER = "это КП";

export const OFFER_ARTICLES_TITLE = "Статьи: где дороже и где ниже";
export const OFFER_COLUMN = {
  article: "Статья",
  perSqm: "КП, ₽/м²",
  whole: "Статья целиком",
  bars: "По строкам: ← ниже · дороже →",
  above: "Дороже расценки",
  below: "Ниже платили",
  none: "Не с чем / разные ед.",
} as const;
export const OFFER_TOTAL_LABEL = "Итого на м² объекта";

export const LINE_COLUMN = {
  work: "Работа в КП · вариант",
  unit: "Ед.",
  quantity: "Объём",
  kpPrice: "Цена КП",
  fork: "Наши договоры: вилка, ₽",
  ours: "Наша расценка",
  toOurs: "К нашей расценке",
} as const;

export const EMPTY_LINES_LABEL = "В этой статье строк под фильтр нет.";
export const ALL_CLASSES_LABEL = "все кл.";
export const SECONDARY_MARK = "ⓘ";
export const SUB_KOPECK_LABEL = "< 0,01";

export const POSITION_LABEL = {
  above: "выше всего, что платили",
  in: "в нашей вилке",
  below: "ниже всего, что платили",
} as const;

export const RUNG_LEVEL_LABEL = { v: "вариант", p: "по параметру", f: "вся семья" } as const;

export function contractsShort(n: number): string {
  return `${n} дог.`;
}
