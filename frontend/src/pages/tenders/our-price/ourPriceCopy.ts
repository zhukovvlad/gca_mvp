import { formatDecimalMoney, formatNumber, pluralRu, roundDecimal } from "@/lib/format";
import type {
  Decimal,
  NoHistoryReason,
  OurPriceLine,
  OurPriceLineState,
  OurPriceParticipant,
  OurPriceTotals,
  ResetReason,
  SectionZone,
  TargetResetReason,
  WorkRung,
} from "@/types/domain";

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
  no_zone: "зона раздела не определена",
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

/** Выгрузка файла подрядчику (спека 4б §2.14). */
export const KP_FILE_EXPORT_LABEL = "Выгрузить КП с нашими расценками";
export const KP_FILE_BLOCKED_NOTE = "база НДС КП неизвестна";

/** Назначение базы НДС КП вручную (спека 4б §2.12). */
export const VAT_BASE_ASSIGN_LABEL = "Назначить базу НДС";
export const VAT_BASE_DIALOG_TITLE = "База НДС КП";
export const VAT_BASE_DIALOG_DESCRIPTION =
  "Ставка, которой соответствуют суммы КП. Файл подрядчику и сравнение строятся в этой базе.";
export const VAT_BASE_FIELD_LABEL = "Ставка НДС, %";
export const VAT_BASE_SAVE_LABEL = "Сохранить";
export const VAT_BASE_SAVE_FALLBACK = "Не удалось сохранить базу НДС.";

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
export const KP_NOT_ADJUSTED_NOTE = "КП не приводится — он уже в ценах этапа";

/** Подпись приведения: предложение ответа без точки и пояснение, что приводится только история (спека 4а §2.10). */
export function inflationCaptionOf(sentence: string): string {
  return `${sentence.replace(/\.$/, "")}; ${KP_NOT_ADJUSTED_NOTE}`;
}
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
  overLowest: "КП выше самых низких расценок",
  overFreshest: "КП выше самых свежих расценок",
  below: "Ниже самой низкой расценки",
  unitMismatch: "Разные единицы",
  notComparable: "Не с чем сравнить",
} as const;

/**
 * Подпись покрытия у суммы «КП выше самых свежих расценок» (спека лестницы аналогов §2.12):
 * самой свежей расценки может не быть у части сравнимых строк, и тогда сумма не по всем.
 * Пустое покрытие даёт `null` у суммы - «—», а не ноль; ноль значит «нигде не выше».
 */
export function freshestCoverageNote(
  totals: Pick<OurPriceTotals, "comparable_lines" | "freshest_lines">
): string | null {
  const { comparable_lines: comparable, freshest_lines: freshest } = totals;
  if (comparable === 0) return null;
  if (freshest === 0) return "у сравнимых строк нет датированных расценок";
  if (freshest < comparable) return `по ${formatNumber(freshest)} из ${formatNumber(comparable)} строк`;
  return null;
}

export function linesCount(n: number): string {
  return `${formatNumber(n)} стр.`;
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
  return `Каталог ещё разбирает строки КП: разобрано ${formatNumber(done)} из ${formatNumber(total)}`;
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

interface SubtitleParts {
  heldOn: string | null;
  objectTitle: string;
  area: Decimal | null;
  rateClass: string;
}

function subtitleOf(first: string | null, parts: SubtitleParts): string {
  return [
    first,
    `объект ${parts.objectTitle}`,
    parts.area !== null ? `${formatDecimalMoney(parts.area, "", 2)} м²` : null,
    `класс ${parts.rateClass}`,
  ]
    .filter((part): part is string => part !== null)
    .join(" · ");
}

export function roundSubtitle(parts: SubtitleParts): string {
  return subtitleOf(parts.heldOn !== null ? dateRu(parts.heldOn) : null, parts);
}

/** Подзаголовок разбора участника: «Этап 3, 10.03.2026 · объект …». */
export function offerSubtitle(stageNo: number, parts: SubtitleParts): string {
  const stage = `Этап ${stageNo}`;
  return subtitleOf(parts.heldOn !== null ? `${stage}, ${dateRu(parts.heldOn)}` : stage, parts);
}

/** Подпись варианта строки: подпись уже начинается с семьи, поэтому семья отдельно не печатается. */
export function variantCaption(line: { family: string | null; variant_label: string | null }): string | null {
  return line.variant_label ?? line.family;
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
  above_lowest: CARD_LABEL.overLowest,
  above_freshest: CARD_LABEL.overFreshest,
  below_lowest: CARD_LABEL.below,
  unit_mismatch: CARD_LABEL.unitMismatch,
  not_comparable: CARD_LABEL.notComparable,
};

/** Подписи плиток нейтральные: у «выше» подписи нет, только счёт строк (спека лестницы аналогов §2.12). */
export const FILTER_HINT: Partial<Record<Exclude<OfferFilter, "all">, string>> = {
  below_lowest: "риск допработ и замены",
  unit_mismatch: "вопрос подрядчику про единицу учёта",
  not_comparable: "не разобраны, нет в договорах, нет цены",
};

export const PER_SQM_TILE_LABEL = "КП на м² объекта";
export const PER_SQM_UNIT = "₽/м²";

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
  ours: "Самая низкая",
  toOurs: "К самой низкой",
} as const;

export const EMPTY_LINES_LABEL = "В этой статье строк под фильтр нет.";

/** Название корня «Нераспределённое»: у него нет статьи в классификаторе, название то же, что отдаёт сервер. */
export const UNALLOCATED_TITLE = "Нераспределённое";

/** Дерево статей и разделов сметы на странице участника (спека упрощения каталога §2.10). */
export const CHAPTER_MARK = "в смете";
export const PATH_SEPARATOR = " › ";
export const TREE_RESTORE_LABEL = "Вернуть дерево";
export const NO_PRICE_FILTER_LABEL = "Строки без цены в КП";
export const NO_PRICE_FILTER_HINT = "цена 0 или пусто — «цены нет»";
export const NO_PRICE_MODE_LABEL: Record<"show" | "hide" | "only", string> = {
  show: "показывать",
  hide: "скрыть",
  only: "только они",
};

/** «1 строка», «2 строки», «5 строк»: форма слова по числу. */
export function rowsLabel(count: number): string {
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return `${count} строка`;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return `${count} строки`;
  return `${count} строк`;
}

export const noPriceCountLabel = (count: number): string => `${count} без цены`;
export const nodeKpLabel = (amount: string): string => `КП ${amount}`;
export const ALL_CLASSES_LABEL = "все кл.";
export const SECONDARY_MARK = "ⓘ";
export const SUB_KOPECK_LABEL = "< 0,01";

export const POSITION_LABEL = {
  above: "выше всего, что платили",
  in: "в нашей вилке",
  below: "ниже всего, что платили",
} as const;

export const RUNG_LEVEL_LABEL = { v: "вариант", p: "по параметру", f: "вся семья" } as const;

/** Зона строки-работы в подписях панели. */
export const ZONE_CAPTION: Record<SectionZone, string> = {
  underground: "подземная часть",
  aboveground: "надземная часть",
  territory: "территория",
  whole: "весь объект",
};

/** Причина `no_history` у работы (спека лестницы аналогов §2.10): текстом, а не кодом. */
export const NO_HISTORY_REASON_LABEL: Record<Exclude<NoHistoryReason, "no_zone_match">, string> = {
  no_family: "в договорах нет работ этой семьи",
  path_conflict: "нет аналога: параметры расходятся по разделам сметы",
  no_params: "нет аналога с этими параметрами",
};

export function noZoneMatchLabel(zone: SectionZone | null): string {
  return zone === null ? "нет аналога в зоне строки" : `нет аналога в зоне «${ZONE_CAPTION[zone]}»`;
}

/** Строка без варианта или без зоны, чьё решение ждёт администратора (спека 4а §2.11, лестницы аналогов §2.10). */
export function isWaitingAdminLine(line: OurPriceLine): boolean {
  return (line.state === "no_variant" || line.state === "no_zone") && line.waiting_admin;
}

/** Подпись состояния рядом с разницей; у сопоставимой строки — её положение в вилке. */
export function lineStateLabel(line: OurPriceLine): string | null {
  if (line.state === "comparable") return line.position === null ? null : POSITION_LABEL[line.position];
  if (isWaitingAdminLine(line)) return WAITING_ADMIN_LABEL;
  if (line.state === "no_history" && line.no_history_reason !== null) {
    return line.no_history_reason === "no_zone_match"
      ? noZoneMatchLabel(line.zone)
      : NO_HISTORY_REASON_LABEL[line.no_history_reason];
  }
  return LINE_STATE_LABEL[line.state];
}

function analogsCount(n: number): string {
  return `${formatNumber(n)} ${pluralForm(n, "аналог", "аналога", "аналогов")}`;
}

/** Подпись ступени работы (спека лестницы аналогов §2.8): «тот же вариант», «без …», «… · другой класс». */
export function workRungLabel(rung: WorkRung): string {
  const base = rung.no === 1 || rung.no === 3 ? "тот же вариант" : `без ${rung.released.join(", ")}`;
  return rung.other_class ? `${base} · другой класс` : base;
}

/** Подпись под ценой в таблице строк: ступень и число аналогов; без ступени подписи нет. */
export function workRungCaption(line: Pick<OurPriceLine, "work_rung" | "analogs_count">): string | null {
  if (line.work_rung === null) return null;
  return `${workRungLabel(line.work_rung)} · ${analogsCount(line.analogs_count)}`;
}

/** Метка цели в таблице строк, если цель — не самая низкая и не «договорились». */
export const TARGET_MARK: Partial<Record<NonNullable<OurPriceLine["target_kind"]>, string>> = {
  freshest: "цель: самая свежая",
  analog: "цель: аналог",
};

/** Строка сравнения панели работы: «7 аналогов в 4 договорах». */
export function analogsInContracts(analogs: number, contracts: number): string {
  return `${analogsCount(analogs)} в ${formatNumber(contracts)} ${pluralForm(contracts, "договоре", "договорах", "договорах")}`;
}

/** «2025-03» → «03.2025»; перестановкой строки. */
export function periodRu(period: string | null): string {
  return period === null ? "—" : period.split("-").reverse().join(".");
}

/** Панель строки (спека 4а §2.12, §2.14): тексты. */
export const SHEET_TITLE = "Строка КП";
export const SHEET_ERROR_LABEL = "Не удалось открыть строку";
export const SHEET_DESCRIPTION = "Сравнение строки КП с нашими договорами";
export const SHEET_SECTION = {
  prices: "Цены",
  comparison: "Сравнение",
  analogs: "Аналоги",
  ladder: "Лестница сравнения",
  family: "Вся семья в этом КП",
  contracts: "Договоры на этой ступени",
  vatDropped: "Договоры, не попавшие в сравнение",
  export: "Для выгрузки",
} as const;
export const SHEET_FIELD = {
  kpPrice: "Цена КП",
  kpTotal: "Сумма строки в КП",
  quantity: "Объём",
  toOurs: "К самой низкой",
  ours: "Самая низкая (справкой)",
  secondary: "Второстепенные",
} as const;

/** Карточки цифр работы (спека лестницы аналогов §2.20): подписи и подсказки. */
export const FIGURE_LABEL = { lowest: "Самая низкая расценка", freshest: "Самая свежая расценка" } as const;
export const FIGURE_NONE = "нет аналога с датой цены";
export const FIGURE_KP_ABOVE = "КП выше";
export const FIGURE_KP_BELOW = "КП ниже";
export const FIGURE_KP_EQUAL = "КП на уровне расценки";

/** Справка аналогов. */
export const ANALOGS_COLUMN = {
  contract: "Объект, договор",
  line: "Строка договора",
  date: "Дата",
  price: "Цена за ед.",
} as const;
export const ANALOG_FIRST = 5;
export function analogsMore(n: number): string {
  return `ещё ${formatNumber(n)} ${pluralForm(n, "аналог", "аналога", "аналогов")}`;
}
export const ANALOGS_LESS = "свернуть";
export const ANALOG_LOWEST = "самая низкая";
export const ANALOG_FRESHEST = "свежая";
export const ANALOG_THIS_CONTRACTOR = "договор с этим подрядчиком";
export const ANALOG_MAKE_TARGET = "целью";
export const ANALOG_IS_TARGET = "цель";
export const ANALOGS_EMPTY = "Аналогов на этой ступени нет.";
export const ANALOGS_NOMINAL_TITLE = "номинал в договоре";

/** Кратность выброса: «×N к остальным — проверьте единицу учёта». */
export function analogOutlierNote(ratio: Decimal): string {
  const { text, smaller } = ratioParts(ratio);
  return `${smaller ? `в ${text} раз меньше остальных` : `×${text} к остальным`} — проверьте единицу учёта`;
}

/** Блок «Для выгрузки»: цель строки-работы (спека лестницы аналогов §2.14, §2.20). */
export const TARGET_LABEL = "Цель";
export const TARGET_TEXT = {
  agreed: "договорённая цена",
  lowest: "самая низкая расценка",
  freshest: "самая свежая расценка",
  analog: "аналог",
} as const;
export const TARGET_AUTO_TAG = "авто";
export const TARGET_MAKE_FRESHEST = "Сделать целью самую свежую";
export const TARGET_BACK_TO_LOWEST = "Вернуть к самой низкой";
export const TARGET_HINT = "или «сделать целью» у аналога в справке";
export function targetAnalogText(objectTitle: string | null, number: string | null): string {
  const named = [objectTitle, number === null ? null : `дог. ${number}`].filter((p): p is string => p !== null);
  return named.length === 0 ? TARGET_TEXT.analog : `${TARGET_TEXT.analog} — ${named.join(", ")}`;
}
export const TARGET_RESET_REASON: Record<TargetResetReason, string> = {
  kind_changed: "строка стала системой",
  not_comparable: "строка перестала быть сравнимой",
  analog_unavailable: "аналога больше нет на ступени",
  no_freshest: "самой свежей расценки больше нет",
};
export function targetInactiveNote(reason: TargetResetReason | null): string {
  const why = reason === null ? "" : `: ${TARGET_RESET_REASON[reason]}`;
  return `Выбор не действует${why} — цель: самая низкая`;
}

/** Плашка «Разные единицы учёта?» работы: все аналоги расходятся между собой. */
export const UNIT_WARN_ALL_OUTLIERS = "Аналоги расходятся между собой — самой низкой и самой свежей расценки нет.";

/** Ссылка у `no_zone`: очередь «Зоны» с подсветкой цепочки (спека лестницы аналогов §2.10). */
export function zoneQueueHref(chainId: number): string {
  return `/families?tab=suggestions&queue=zones&chain=${chainId}`;
}

/**
 * Подпись цен панели строки. В ставке КП без ставки (база НДС неизвестна) поле «в ставке КП»
 * пусто, и называть такие цифры «без НДС» нельзя: подпись говорит, что базы нет.
 */
export function priceBasisLabel(view: "net" | "shown", rate: Decimal | null): string {
  if (view === "net") return VAT_NET_LABEL;
  return rate === null ? VAT_UNKNOWN_LABEL : `${vatShownLabel(rate)} (договоры — по ставке КП)`;
}

export const RUNG_SCOPE_LABEL = { cls: "класс объекта", all: "все классы" } as const;
export const RUNG_AUTO_TAG = "авто";

/** Решения человека по строке: блок «Для выгрузки» и сохраняемая ступень (спека 4б §2.3–§2.6, §2.14). */
export const EXPORT_OURS_LABEL = "Наша расценка";
export const EXPORT_OURS_NOTE = "считается из данных и следует за ними";
export const EXPORT_OURS_NONE_NOTE = "цены за единицу в файле не будет";
export const SAVE_LABEL = "Сохранить";
export const SAVING_LABEL = "Сохраняем…";

export const AGREED_PLACEHOLDER = "не договаривались";
export const AGREED_HINT =
  "вписываете вы, GCA это число не меняет. В файл подрядчику идёт договорённое число, если оно есть, иначе наша расценка";

/** Подпись поля «Договорились»: единица строки КП и вид цены; единицы нет — без неё. */
export function agreedLabel(unit: string | null, basis: string): string {
  return unit === null ? `Договорились, ${basis}` : `Договорились, ${unit}, ${basis}`;
}

/** Метка под нашей расценкой в таблице строк. */
export function agreedCaption(value: string): string {
  return `договорились ${value}`;
}

export const COMMENT_LABEL = "Комментарий для подрядчика";
export const COMMENT_PLACEHOLDER = "комментария нет";
export const COMMENT_TAG = { template: "шаблон", custom: "свой текст" } as const;
export const COMMENT_TAG_EMPTY = "свой текст (пусто)";
export const COMMENT_TO_TEMPLATE = "к шаблону";
export const COMMENT_NO_TEMPLATE_HINT = "комментария не будет";

export const RUNG_SAVE_LABEL = "Сохранить эту ступень";
export const RUNG_BACK_TO_AUTO_LABEL = "Вернуть авто";
export const RUNG_MANUAL_TAG = "ручная";
export const RUNG_MANUAL_FAMILY_TAG = "ручная · для всей семьи в этом КП";
export const RUNG_MANUAL_INACTIVE_TAG = "ручная не действует";

/** Метка действующей ручной ступени: у ступени семьи — с пояснением, на кого она действует. */
export function manualRungTag(target: "line" | "family"): string {
  return target === "family" ? RUNG_MANUAL_FAMILY_TAG : RUNG_MANUAL_TAG;
}

/** Почему сохранённая ручная ступень не действует: по одному тексту на каждый код ответа. */
export const RUNG_RESET_REASON: Record<ResetReason, string> = {
  kind_changed: "строка стала системой, а ступень выбрана для работы",
  key_changed: "у строки сменился вариант или семья",
  rung_unavailable: "такой ступени у строки больше нет",
  rung_empty: "на этой ступени больше нет договоров",
};

/** Плашка недействующей ручной ступени; код причины может не прийти — тогда без причины. */
export function rungInactiveNote(rungLabel: string, reason: ResetReason | null): string {
  const why = reason === null ? "" : `: ${RUNG_RESET_REASON[reason]}`;
  return `Ручная ступень ${rungLabel} не действует${why} — считается автоматическая`;
}
export const RUNG_RESET_LABEL = "Вернуть автоматическую";
export const RUNG_HINT =
  "Нажмите на другую ступень, чтобы сравнить по ней: вилка, наша расценка и отклонение пересчитаются. Цифры страницы разбора при этом не меняются.";

/** Форма существительного по числу: 1 строка, 2 строки, 5 строк (11–14 — всегда «строк»). */
function pluralForm(n: number, one: string, few: string, many: string): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return few;
  return many;
}

export function groupLinesCaption(n: number): string {
  return `${n} ${pluralForm(n, "строка", "строки", "строк")} КП вместе`;
}
export const GROUP_THIS_LINE = "эта строка";
export const GROUP_OUTSIDE = "вне группы";
export function groupMembership(lines: number): string {
  return `группа из ${lines} стр.`;
}

export const CONTRACT_LOWER_BOUND = "нижняя граница";
export const CONTRACT_WITH_CONTRACTOR = "договор с этим подрядчиком";
export function contractNumber(number: string): string {
  return `договор ${number}`;
}
/**
 * Кратность расхождения строкой: один знак после запятой («5,3», целое без «,0»), от ста —
 * целым с разрядами («37 610»). Правило одно для всех кратностей экрана.
 */
function ratioDigits(value: string): string {
  const oneDigit = roundDecimal(value, 1);
  if (oneDigit.split(".")[0].length < 3) return oneDigit.replace(/\.0$/, "").replace(".", ",");
  return formatNumber(Number(roundDecimal(value, 0)));
}

/**
 * Кратность меньше единицы переворачивается: «0,0000769» читается как «в 13 004 раз меньше».
 * Кратность — отношение цен, а не деньги: переворот считается числом, без денег на клиенте.
 */
function ratioParts(ratio: Decimal): { text: string; smaller: boolean } {
  const value = Number(ratio);
  const smaller = value > 0 && value < 1;
  return { text: ratioDigits(smaller ? (1 / value).toFixed(6) : ratio), smaller };
}

export function formatRatio(ratio: Decimal): string {
  const { text, smaller } = ratioParts(ratio);
  return smaller ? `в ${text} раз меньше` : `×${text}`;
}
export function ratioNote(ratio: Decimal): string {
  const { text, smaller } = ratioParts(ratio);
  const subject = smaller ? `в ${text} раз меньше остальных договоров` : `×${text} к остальным договорам`;
  return `${subject} — проверьте единицу учёта`;
}
export const VAT_DROPPED_NOTE = "выпал: неизвестна база НДС";

export const UNIT_WARN_TITLE = "Разные единицы учёта?";
export function unitWarnKp(ratio: Decimal, median: string | null): string {
  const { text, smaller } = ratioParts(ratio);
  const verb = smaller ? "ниже" : "отличается от";
  return `Цена КП ${verb} медианы наших договоров${median === null ? "" : ` (${median})`} в ${text} раз.`;
}
export function unitWarnContracts(ratios: Decimal[]): string {
  return `Наши договоры расходятся с остальными: ${ratios.map(formatRatio).join(", ")}.`;
}
export const UNIT_WARN_TAIL =
  "Почти всегда это не цена, а разный способ счёта: в одном месте за единицу, в другом — за весь объём. Ничего не исключено; в суммы переплаты строка не входит. Хороший вопрос подрядчику.";

export function contractsShort(n: number): string {
  return `${n} дог.`;
}

/** Входы на экраны 4а с карточки тендера: кнопка панели этапа и пункт меню ячейки КП. */
export const OUR_PRICE_ENTRY_LABEL = "Против нашей цены";
export const OUR_PRICE_ENTRY_NO_ESTIMATES = "В этапе нет КП со сметой";

/** Страница предпросмотра файла подрядчику (спека 4б §2.9, §2.10, §2.14). */
export const KP_FILE_TITLE = "Файл подрядчику";
export const KP_FILE_DOWNLOAD_LABEL = "Скачать xlsx";
export const KP_FILE_SENT_TITLE = "Отправлено подрядчику";
export const KP_FILE_SENT_EMPTY = "Файлов ещё не отправляли";
export const KP_FILE_SENT_ERROR = "Не удалось загрузить список отправленных файлов";
export const KP_FILE_SENT_DOWNLOAD = "скачать";
/** Ссылка у участника без сметы: страница файла остаётся единственным местом списка отправленных. */
export function kpFileSentLink(n: number): string {
  return `Отправленные файлы (${n})`;
}
export const KP_FILE_YEARS_TITLE = "Коэффициенты приведения по ИПЦ";
export const KP_FILE_FORECAST_TAG = "прогноз";
export const KP_FILE_EDIT_COMMENT_LABEL = "Править комментарий";
export const KP_FILE_CANCEL_LABEL = "Отмена";
export const KP_FILE_ERROR_TITLE = "Предпросмотр не построен";

/** Столбцы листа, которые кладёт наша сторона: справа от столбцов подрядчика, выделены фоном. */
export const KP_FILE_OURS_COLUMNS: ReadonlySet<string> = new Set([
  "ours",
  "deviation_pct",
  "source",
  "inflation",
  "comment",
]);

const KP_FILE_ERROR_NOT_FOUND = "Этап, тендер или КП не найдены";
const KP_FILE_ERROR_GENERIC = "Не удалось построить предпросмотр файла";

/** Подпись отказа предпросмотра: 404 с выбранным рядом печатает сообщение сервера, оно называет, чего нет. */
export function kpFileErrorLabel(status: number | undefined, seriesSelected: boolean, serverMessage?: string): string {
  if (status === 404 && seriesSelected && serverMessage) return serverMessage;
  return status === 404 ? KP_FILE_ERROR_NOT_FOUND : KP_FILE_ERROR_GENERIC;
}

/** Плашка «каталог ещё разбирает»: у этих строк в файле не будет наших цен. */
export function kpFileInProgressNote(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  const noun = mod10 === 1 && mod100 !== 11 ? "строку" : mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20) ? "строки" : "строк";
  return `Каталог ещё разбирает ${formatNumber(n)} ${noun} — в файле у них не будет наших цен`;
}
