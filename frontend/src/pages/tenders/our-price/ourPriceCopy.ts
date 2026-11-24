import { formatDate, formatDecimalMoney, formatNumber, pluralRu, roundDecimal } from "@/lib/format";
import type {
  Decimal,
  LumpView,
  MarkKind,
  NoHistoryReason,
  OurPriceAnalog,
  OurPriceLine,
  OurPriceLineState,
  OurPriceParticipant,
  OtherZone,
  OurPriceTotals,
  RefRow,
  ResetReason,
  SectionZone,
  Zone,
  TargetResetReason,
  WorkRung,
  ZoneScope,
} from "@/types/domain";

import { SECTION_ZONES, ZONE_LABEL } from "@/pages/families/labels";

import type { OfferFilter } from "./offerFilter";
import type { VatView } from "./money";
import { lowerFirst } from "./paramsCopy";

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
  lump_sum: "Похоже на комплект",
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
  not_comparable: "не разобраны, нет в договорах, нет цены, похожие на комплект",
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
export const NO_HISTORY_REASON_LABEL: Record<Exclude<NoHistoryReason, "no_zone_match" | "composite">, string> = {
  no_family: "в договорах нет работ этой семьи",
  path_conflict: "нет аналога: параметры расходятся по разделам сметы",
  no_params: "нет аналога с этими параметрами",
  excluded: "Аналоги исключены вручную",
};

/** Сборная строка без сборных аналогов (спека упрощения каталога §2.8): имя параметра — из ответа строки. */
export function compositeNoHistoryLabel(parameterName: string | null): string {
  const base = "нет аналога: в строке несколько значений параметра";
  return parameterName === null ? base : `${base} «${parameterName}»`;
}

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
    if (line.no_history_reason === "no_zone_match") return otherZonesLabel(line.zone, line.other_zones ?? []);
    if (line.no_history_reason === "composite") return compositeNoHistoryLabel(line.composite_parameter?.name ?? null);
    return NO_HISTORY_REASON_LABEL[line.no_history_reason];
  }
  return LINE_STATE_LABEL[line.state];
}

/** Подпись единицы в родительном падеже для мерных кодов: «а не квадратного метра» (спека комплектов §8 п. 2). */
const UNIT_GENITIVE: Record<string, string> = {
  "м": "метра",
  "м2": "квадратного метра",
  "м²": "квадратного метра",
  "м3": "кубического метра",
  "м³": "кубического метра",
  "т": "тонны",
  "кг": "килограмма",
};

/** Единица в родительном падеже; у прочих («шт», «компл») склонять нечем — «ставки за {единица}». */
export function lumpUnitGenitive(unit: string): string {
  return UNIT_GENITIVE[unit.trim().toLowerCase()] ?? `ставки за ${unit}`;
}

/** «медиана N строк»: число строк в родительном падеже («3 строк», «21 строки»), как в тексте спеки комплектов §8 п. 2. */
function lumpRowsGenitive(count: number): string {
  const one = count % 10 === 1 && count % 100 !== 11;
  return `${formatNumber(count)} ${one ? "строки" : "строк"}`;
}

/** Кратность комплекта: до целых с разделителем разрядов («16 989»). */
function lumpRatioText(ratio: Decimal): string {
  return formatNumber(Number(roundDecimal(ratio, 0)));
}

/** Подпись под меткой «Похоже на комплект» в таблице строк (спека комплектов §8 п. 1). */
export function lumpCaption(lump: LumpView): string {
  if (lump.ratio === null || lump.typical === null) return "обычной ставки нет";
  return `цена в ${lumpRatioText(lump.ratio)} раз выше обычной ставки семьи`;
}

/** Цена панели в нужном виде; нет показанной цены (база НДС неизвестна) — нетто. */
function lumpMoney(pair: { net: Decimal; shown: Decimal | null }, vat: VatView): { text: string; shown: boolean } {
  const shown = vat === "shown" && pair.shown !== null;
  return { text: formatDecimalMoney(shown ? pair.shown : pair.net, "₽", 2), shown };
}

/** Поправка у строки: «поправлено вручную: {email}, {дата}»; без поправки `null`. */
export function lumpOverrideNote(lump: LumpView): string | null {
  if (lump.override === null) return null;
  return `поправлено вручную: ${lump.override.set_by.email}, ${formatDate(lump.override.set_at)}`;
}

/**
 * Пояснение «Сравнения» строки-комплекта (спека комплектов §8 п. 2): три вида — ставка есть,
 * ставки нет, поправка «это комплект» при `rule = false`. `vatRate` — ставка КП для подписи
 * «с НДС {ставка} %»; без неё цены идут нетто.
 */
export function lumpWhy(lump: LumpView, vat: VatView, vatRate: Decimal | null = null): string {
  if (lump.override !== null && lump.override.verdict === "lump" && !lump.rule) {
    return `Поправлено вручную: ${lump.override.set_by.email}, ${formatDate(lump.override.set_at)}. Правило считает строку ставкой.`;
  }
  // Без ставки КП «с НДС» подписать нечем: цены идут нетто, а не показанными без подписи.
  const view: VatView = vat === "shown" && vatRate !== null ? "shown" : "net";
  const price = lumpMoney(lump.unit_price, view);
  if (lump.typical === null || lump.ratio === null) {
    return `В смете: 1 ${lump.unit} × ${price.text}. Обычной ставки у семьи «${lump.family}» в этой единице нет (строк с настоящим объёмом меньше трёх), а одна ${lump.unit} за такую цену — похоже на комплект.`;
  }
  const typical = lumpMoney(lump.typical, view);
  const basis = price.shown && vatRate !== null ? vatShownLabel(vatRate) : VAT_NET_LABEL;
  return `В смете: 1 ${lump.unit} × ${price.text} ${basis}. Обычная ставка семьи «${lump.family}» — ${typical.text} за ${lump.unit} (медиана ${lumpRowsGenitive(lump.typical_lines)} с настоящим объёмом). Цена строки выше в ${lumpRatioText(lump.ratio)} раз: похоже на цену комплекта, а не ${lumpUnitGenitive(lump.unit)}. Со ставками договоров её не сравнить.`;
}

/** Блок поправки «Комплект или ставка» (спека комплектов §8 п. 3). */
export const LUMP_BLOCK_TITLE = "Комплект или ставка";
export const LUMP_BLOCK_HINT =
  "Поправка хранится у этой строки сметы и действует на всех экранах. «Это ставка» вернёт строку в обычное сравнение.";
export const LUMP_COMPARISON_NOTE = "не сравнивается";
export const LUMP_CHOICE_LABEL = { rule: "Как решило правило", lump: "Это комплект", rate: "Это ставка" } as const;

/** Действие в списке аналогов (спека комплектов §8 п. 4) и его подтверждение. */
export const LUMP_ANALOG_ACTION = "это комплект — убрать из сравнения";
export function lumpAnalogConfirm(title: string): string {
  return `Строка договора «${title}» перестанет служить аналогом во всех КП. Вернуть — в «Найти в договорах».`;
}
export const LUMP_ANALOG_CONFIRM_TITLE = "Убрать строку из сравнения?";
export const LUMP_ANALOG_CONFIRM_CANCEL = "Отмена";

/** Отметки «не аналог» (спека «Не аналог» §8, макет разделы 1-4): меню аналога, окна, метки, пары, причина. */
export const MARK_MENU_LABEL = "Строка договора";
export const MARK_MENU_TRIGGER = "Действия со строкой договора";
export const MARK_ACTION: Record<MarkKind, string> = {
  tender: "Не аналог в этом тендере…",
  price_error: "Ошибка в цене — не аналог нигде…",
};
export const MARK_DIALOG_TITLE: Record<MarkKind, string> = {
  tender: "Не аналог в этом тендере",
  price_error: "Ошибка в цене — не аналог нигде",
};
export const MARK_REASON_LABEL = "Причина";
export const MARK_REASON_MAX = 300;
export const MARK_SUBMIT = "Исключить";
export const MARK_CANCEL = "Отмена";
export const MARK_KIND_LABEL: Record<MarkKind, string> = {
  price_error: "ошибка в цене · во всех тендерах",
  tender: "не аналог в этом тендере",
};
export const MARK_CLEAR: Record<MarkKind, string> = {
  tender: "Снять в этом тендере",
  price_error: "Снять во всех тендерах…",
};
export const MARK_CLEAR_CONFIRM_TITLE = "Снять «ошибку в цене»?";
export const MARK_CLEAR_CONFIRM_OK = "Снять везде";
export function markReasonCounter(length: number): string {
  return `${length} / ${MARK_REASON_MAX}`;
}
/** Область действия окна отметки (макет, разделы 1-2): строка договора, кого и где она перестаёт быть аналогом. */
export function markScopeText(
  kind: MarkKind,
  row: { title: string; objectTitle: string },
  form: string,
  tenderId: number
): string {
  if (kind === "price_error") {
    return `Строка «${row.title}» договора ${row.objectTitle} перестанет быть аналогом во всех тендерах и выпадет из обычной ставки семьи. Отметку можно снять.`;
  }
  return `Строка «${row.title}» договора ${row.objectTitle} не будет аналогом для строки формы «${form}» у всех участников и во всех этапах тендера ${tenderId}. В других тендерах она остаётся аналогом.`;
}
/** Подтверждение снятия «ошибки в цене» (макет, раздел 4): действует во всех тендерах. */
export function markClearConfirmText(
  row: { title: string; objectTitle: string },
  family: string | null,
  tenderId: number | null
): string {
  const tender = tenderId === null ? "" : `, кроме тех, где на ней стоит отметка «не аналог в этом тендере» (здесь — тендер ${tenderId})`;
  const rates = family === null ? "в обычную ставку семьи" : `в обычную ставку семьи «${family}»`;
  return `Строка «${row.title}» договора ${row.objectTitle} вернётся в аналоги во всех тендерах${tender}, и снова войдёт ${rates}.`;
}
export const EXCLUDED_HINT = "не входят в вилку и медиану";
export function excludedTitle(count: number): string {
  return `Исключены (${count})`;
}
export const EXCLUDED_NO_COMPARE = "Найденные строки договоров отмечены «не аналог». Сравнить не с чем.";
export const ZONE_SCOPE_AREA: Record<ZoneScope, string> = { own: "своя зона", other: "другие зоны", any: "любые зоны" };
/** Область в предложном падеже: «на ступени 4 своей зоны». */
const ZONE_SCOPE_OF: Record<ZoneScope, string> = { own: "своей зоны", other: "других зон", any: "любых зон" };
/** Подпись пары следа над карточками: «ступень 4 · своя зона». */
export function excludedPairLabel(step: { rung: number; zone_scope: ZoneScope }): string {
  return `ступень ${step.rung} · ${ZONE_SCOPE_AREA[step.zone_scope]}`;
}
/** Метка карточки, стоявшей не на паре, где подбор остановился. */
export function excludedWouldBe(step: { rung: number; zone_scope: ZoneScope }): string {
  return `был бы на ступени ${step.rung} · ${ZONE_SCOPE_AREA[step.zone_scope]}`;
}
/** «Ступень 4, другие зоны: на ступени 4 своей зоны аналоги исключены вручную (1)»; пары — через «;». */
export function excludedMovedOn(
  work: { rung: number; zone_scope: ZoneScope },
  skipped: Array<{ step: { rung: number; zone_scope: ZoneScope }; count: number }>
): string {
  const parts = skipped.map(
    (s) => `на ступени ${s.step.rung} ${ZONE_SCOPE_OF[s.step.zone_scope]} аналоги исключены вручную (${s.count})`
  );
  return `Ступень ${work.rung}, ${ZONE_SCOPE_AREA[work.zone_scope]}: ${parts.join("; ")}`;
}

/** «Найти в договорах» (спека комплектов §8 п. 5): метка, пометка поправки, подсказка у «сделать целью». */
export const LUMP_SEARCH_MARK = "похоже на комплект";
export const LUMP_SEARCH_OVERRIDE = "поправлено вручную";
/** Подсказка недоступного «сделать целью» у отмеченной строки поиска (спека «Не аналог» §8 п. 5). */
export const EXCLUDED_NOT_TARGET = "Исключена из аналогов";
export const LUMP_NOT_TARGET = "Похоже на комплект — не цель для ставки";
export const LUMP_ROW_MENU = "Комплект или ставка";

/** «Как решило правило (комплект)» — ответ правила в скобках. */
export function lumpRuleChoiceLabel(lump: LumpView): string {
  return `${LUMP_CHOICE_LABEL.rule} (${lump.rule ? "комплект" : "ставка"})`;
}

function analogsCount(n: number): string {
  return `${formatNumber(n)} ${pluralForm(n, "аналог", "аналога", "аналогов")}`;
}

/**
 * Подпись ступени работы (спека лестницы аналогов §2.8, спека параметров варианта §2.6):
 * «тот же вариант», «без учёта: …», «… · другой класс».
 */
export function workRungLabel(rung: Pick<WorkRung, "no" | "released" | "other_class">): string {
  const base =
    rung.no === 1 || rung.no === 3 ? "тот же вариант" : `без учёта: ${rung.released.map(lowerFirst).join(", ")}`;
  return rung.other_class ? `${base} · другой класс` : base;
}

/** Подпись под ценой в таблице строк: ступень и число аналогов; без ступени подписи нет. */
export function workRungCaption(line: Pick<OurPriceLine, "work_rung" | "analogs_count">): string | null {
  if (line.work_rung === null) return null;
  const mark = zoneScopeMark(line.work_rung.zone_scope);
  return `${workRungLabel(line.work_rung)} · ${analogsCount(line.analogs_count)}${mark === null ? "" : ` · ${mark}`}`;
}

/** Метка области подбора по зоне (спека зоны как предпочтения §5 п. 1-2): у своей зоны её нет. */
export function zoneScopeMark(scope: ZoneScope): string | null {
  if (scope === "other") return "другая зона";
  if (scope === "any") return "зона не определена";
  return null;
}

/** Пояснение к области `other`: из каких зон взяты аналоги (зоны - в порядке `SECTION_ZONES`, строки без зоны - последними). */
export function otherZonesWhy(lineZone: SectionZone, analogs: Pick<OurPriceAnalog, "zone">[]): string {
  const items: string[] = [];
  let zoned = 0;
  for (const zone of SECTION_ZONES) {
    const n = analogs.filter((a) => a.zone === zone).length;
    if (n === 0) continue;
    zoned += 1;
    items.push(`«${zoneTitle(zone)}» (${n})`);
  }
  const unzoned = analogs.filter((a) => a.zone === null).length;
  const head = zoned === 0 ? "" : zoned === 1 ? "зоны " : "зон ";
  const tail = unzoned > 0 ? [`строк без зоны (${unzoned})`] : [];
  const parts = [...items, ...tail];
  const list = parts.length > 1 ? `${parts.slice(0, -1).join(", ")} и ${parts[parts.length - 1]}` : parts.join("");
  return `В зоне «${zoneTitle(lineZone)}» аналогов нет — взяты из ${head}${list}.`;
}

export const ANY_ZONE_WHY = "Зона раздела не определена — аналоги из всех зон. Когда зону выберут, сравнение пересчитается.";

/** Метка зоны аналога в справке панели (при области не `own`). */
export function analogZoneBadge(zone: SectionZone | null): string {
  return zone === null ? "зона: не определена" : `зона: ${ZONE_LABEL[zone]}`;
}

/** Семья с флажком «зона обязательна» (спека §5 п. 4): строка над справкой и префикс к «Сравнению». */
export function zoneRequiredNote(family: string): string {
  return `У семьи «${family}» зона обязательна: аналоги из других зон не берутся в сравнение. Они ниже — для справки.`;
}

export function zoneRequiredNoZonePrefix(family: string): string {
  return `У семьи «${family}» зона обязательна. `;
}

/** Метка цели в таблице строк, если цель — не самая низкая и не «договорились». */
export const TARGET_MARK: Partial<Record<NonNullable<OurPriceLine["target_kind"]>, string>> = {
  freshest: "цель: самая свежая",
  contractor: "цель: с этим подрядчиком",
  analog: "цель: аналог",
  reference: "цель: справочная",
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
  where: "Где строка",
  prices: "Цены",
  comparison: "Сравнение",
  analogs: "Аналоги",
  ladder: "Лестница сравнения",
  family: "Вся семья в этом КП",
  contracts: "Договоры на этой ступени",
  vatDropped: "Договоры, не попавшие в сравнение",
  parts: "По частям, для справки",
  export: "В файл подрядчику",
} as const;
export const SHEET_FIELD = {
  kpPrice: "Цена КП",
  kpTotal: "Сумма строки в КП",
  quantity: "Объём",
  toOurs: "К самой низкой",
  ours: "Самая низкая (справкой)",
} as const;

/** Карточки цифр работы (спека лестницы аналогов §2.20): подписи и подсказки. */
export const FIGURE_LABEL = { lowest: "Самая низкая расценка", freshest: "Самая свежая расценка" } as const;
export const FIGURE_NONE = "нет аналога с датой цены";
export const FIGURE_KP_ABOVE = "КП выше";
export const FIGURE_KP_BELOW = "КП ниже";
export const FIGURE_KP_EQUAL = "КП на уровне расценки";

/** Справка аналогов: не больше пяти, остальные за «ещё N аналога из M». */
export const ANALOG_FIRST = 5;
export function analogsMore(hidden: number, total: number): string {
  return `ещё ${formatNumber(hidden)} ${pluralForm(hidden, "аналог", "аналога", "аналогов")} из ${formatNumber(total)}`;
}
export const ANALOGS_LESS = "свернуть";
export const ANALOG_THIS_CONTRACTOR = "договор с этим подрядчиком";
export const ANALOG_MAKE_TARGET = "сделать целью";
export const ANALOGS_EMPTY = "Аналогов на этой ступени нет.";
export const ANALOGS_NOMINAL_TITLE = "номинал в договоре";

/** Кратность выброса: «×N к остальным — проверьте единицу учёта». */
export function analogOutlierNote(ratio: Decimal): string {
  const { text, smaller } = ratioParts(ratio);
  return `${smaller ? `в ${text} раз меньше остальных` : `×${text} к остальным`} — проверьте единицу учёта`;
}

/** Карточки цели строки-работы (спека упрощения каталога §2.9, §2.11): по стратегии, без объединения. */
export const TARGET_TEXT = {
  agreed: "договорённая цена",
  lowest: "самая низкая расценка",
  freshest: "самая свежая расценка",
  contractor: "расценка с этим подрядчиком",
  analog: "выбранный аналог",
} as const;
export const TARGET_CARD = {
  lowest: "самая низкая",
  freshest: "самая свежая",
  contractor: "с этим подрядчиком",
  pinned: "выбранный аналог",
  reference: "справочная",
  target: "цель",
  targetAuto: "цель · авто",
  choose: "сделать целью",
} as const;
export function targetSameContract(label: string): string {
  return `тот же договор, что «${label}»`;
}
export const TARGET_RESET_REASON: Record<TargetResetReason, string> = {
  kind_changed: "строка стала системой",
  not_comparable: "строка перестала быть сравнимой",
  analog_unavailable: "аналога больше нет на ступени",
  no_freshest: "самой свежей расценки больше нет",
  no_contractor: "среди аналогов больше нет договора с этим подрядчиком",
  reference_unavailable: "строка договора больше не годится как справочная",
  lump_sum: "комплект вместо ставки",
  excluded: "строка исключена из аналогов",
};
export function targetInactiveNote(reason: TargetResetReason | null): string {
  const why = reason === null ? "" : `: ${TARGET_RESET_REASON[reason]}`;
  return `Выбор не действует${why} — цель: самая низкая`;
}

/** Сборная строка без сборных аналогов (спека упрощения каталога §2.8, §2.11). */
export function COMPOSITE_NO_TARGET(parameterName: string | null): string {
  const param = parameterName === null ? "параметра" : `параметра «${parameterName}»`;
  return `Цели нет: в строке несколько значений ${param}, а строки с таким же набором одной ценой в наших договорах нет. Части — только справкой: в сборной строке своя доля каждой части, сравнивать с ценой одной из них нельзя.`;
}

/** «Где строка»: подписи блоков (спека упрощения каталога §2.11). */
export const WHERE_ARTICLE = "статья классификатора";
export const WHERE_PATH = { kp: "в смете КП", contract: "в смете договора" } as const;

/** Блок «Зона места» панели строки (спека зоны в панели строки §3): тексты. */
export const ZONE_BLOCK_TITLE = "Зона места";
export const ZONE_HINT_LABEL = "Что такое зона";
export const ZONE_HINT =
  "Зона — часть объекта, где выполняется работа: подземная, надземная, территория или весь объект. Её определяют разделы сметы, а не название работы: один и тот же дренаж бывает и над паркингом, и на кровле корпуса. Аналоги строке ищутся сначала в договорах с той же зоной; если там нет ни одного — в договорах других зон. У семей с отметкой «зона обязательна» — только в той же зоне.";
export const ZONE_UNDEFINED = "не определена";
export const ZONE_UNDEFINED_TITLE = "Не определена";
export const ZONE_QUEUE_LINK = "открыть в очереди «Зоны» →";
export const ZONE_READONLY = "Зону раздела меняет администратор.";
export const ZONE_NO_CHAIN =
  "Зона места · не определена — строка стоит вне разделов сметы, зону назначить некуда";
export const ZONE_SOURCE_RULE = "по правилу";
export const ZONE_SOURCE_RUNNING = "модель определяет";

/** Название зоны с заглавной буквы: блок и окно («Подземная часть»). */
export function zoneTitle(zone: SectionZone): string {
  const caption = ZONE_CAPTION[zone];
  return `${caption.charAt(0).toUpperCase()}${caption.slice(1)}`;
}

export function zoneSourceModel(confidence: string | null): string {
  return confidence === null ? "поставила модель" : `поставила модель · ${confidence}`;
}

export function zoneSourceHuman(date: string): string {
  return `поставил человек · ${date}`;
}

/** Охват решения: две разные вещи (спека зоны в панели строки §3 п. 4). */
export function zoneScopeRows(kp: number, contracts: number): string {
  return `Зона назначается строкам раздела во всех сметах: КП — ${kp}, договоры — ${contracts}.`;
}

export function zoneScopeContracts(contracts: number): string {
  return `Строки договоров этого раздела (${contracts}) служат аналогами для строк других КП — в первую очередь той же зоны: решение меняет и их сравнение.`;
}

/** Модель у цепочки без зоны: подпись на каждое состояние (спека §3 п. 5). */
export const ZONE_MODEL_STATE_TEXT = {
  running: "Модель определяет зону.",
  paused: "Модель ждёт сброса лимита провайдера; можно не ждать и выбрать зону.",
  stopped: "Разбор моделью остановлен до решения администратора; зону выбирает администратор.",
  failed: "Модель не ответила; решает администратор.",
  none: "Ответа модели нет; решает администратор.",
} as const;

export function zoneModelProposed(zone: SectionZone, confidence: string, reason: string, threshold: string): string {
  return `Модель предлагает: ${ZONE_CAPTION[zone]} · ${confidence} — «${reason}»; ниже порога ${threshold}, решает администратор.`;
}

export function zoneModelUnknown(reason: string): string {
  return `Модель не смогла определить зону — «${reason}»; решает администратор.`;
}

/** Модель у цепочки с зоной: что она предлагала (спека §3 п. 6). */
export function zoneModelWas(zone: SectionZone | "unknown", confidence: string, reason: string): string {
  return `Модель предлагала: ${zone === "unknown" ? "не знаю" : ZONE_CAPTION[zone]} · ${confidence} — «${reason}»`;
}

/** «Сравнение» у строки в состоянии «зона не определена» (спека §3 п. 3). */
export const NO_ZONE_COMPARE = "Не с чем сравнить: зона раздела не определена";
export const NO_ZONE_COMPARE_ADMIN_TAIL = " — выберите её выше";
export const NO_ZONE_COMPARE_NO_CHAIN = "Не с чем сравнить: строка вне разделов сметы";

/** Ответ панели не подтверждён ошибкой (спека §3 п. 9). */
export const ZONE_STALE_TEXT =
  "Панель не обновилась: зона на экране может быть устаревшей. Обновите панель, прежде чем менять зону.";
export const ZONE_STALE_REFRESH = "Обновить";
export const ZONE_REFRESHING_LABEL = "Обновляем…";

/** Окно подтверждения смены зоны (спека §3 п. 8). */
export const ZONE_DIALOG_TITLE = "Сменить зону раздела?";
export const ZONE_DIALOG_CANCEL = "Отмена";
export const ZONE_DIALOG_CONFIRM = "Сменить зону";
export function zoneDialogChange(titles: string[], from: string, to: string): string {
  return `«${titles.join(" / ")}»: ${from} → ${to}.`;
}

/** Справка «по частям» сборной строки. */
export const PARTS_NOTE = "Целью части не становятся: кнопки «сделать целью» здесь нет.";
export const PARTS_NO_ANALOGS = "аналогов нет";
export const PARTS_NO_RANGE = "вилки нет: аналоги расходятся между собой";
export const PARTS_LOWEST = "самая низкая";
export const PARTS_SAME_CLASS = "тот же класс объекта";
export const PARTS_OTHER_CLASS = "другой класс объекта";

/** Блок «В файл подрядчику»: откуда число (спека упрощения каталога §2.11). */
export const EXPORT_SOURCE = {
  ours: "наша расценка",
  target: (label: string) => `наша расценка · цель: ${label}`,
  agreedOverTarget: "договорились · перекрывает цель",
  agreedManual: "договорились · вписано вручную",
  nothing: "в файл ничего не идёт",
} as const;

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
export const EXPORT_OURS_NOTE = "считается из данных и следует за ними";
export const EXPORT_NOTHING_NOTE = "число появится, если вписать «Договорились»";
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

/**
 * Подпись строки без аналога в своей зоне (спека справочных аналогов §2.5): «нет аналога в зоне «Надземная» ·
 * есть в зоне «Подземная»: 3 строки, 2 договора, «Территория»: 1 строка, 1 договор». Групп нет — прежняя подпись.
 */
export function otherZonesLabel(zone: Zone | null, groups: OtherZone[]): string {
  if (groups.length === 0) return noZoneMatchLabel(zone);
  const head = zone === null ? "нет аналога в зоне строки" : `нет аналога в зоне «${ZONE_LABEL[zone]}»`;
  const found = groups.map((g, index) => {
    const size = `${formatNumber(g.lines)} ${pluralForm(g.lines, "строка", "строки", "строк")}, ${formatNumber(g.contracts)} ${pluralForm(g.contracts, "договор", "договора", "договоров")}`;
    return `${index === 0 ? "есть в зоне " : ""}«${ZONE_LABEL[g.zone]}»: ${size}`;
  });
  return `${head} · ${found.join(", ")}`;
}

// ---------------------------------------------------------------------------
//  Справочные аналоги: блоки А и Г, поиск, диалог пересчёта (спека справочных аналогов §2.4-§2.13)
// ---------------------------------------------------------------------------

export const REF_ZONES_TITLE = "Справочно: та же работа в другой зоне";
export const REF_NOTE = "Справочные строки не входят в вилку, «Самую низкую» и итоги статей. Целью становятся только по вашему выбору.";
export const REF_FIRST = 5;
export const REF_MAKE_TARGET = "сделать целью";
export const REF_THIS_CONTRACTOR = "договор с этим подрядчиком";
export const REF_LESS = "свернуть";
export const REF_OTHER_CLASS = "другой класс";

export function refMore(hidden: number): string {
  return `ещё ${formatNumber(hidden)}`;
}

/** Метка зоны группы блока А. */
export function refZoneBadge(zone: Zone): string {
  return `зона: ${ZONE_LABEL[zone]}`;
}

/** Метка свёртки: «×9». */
export function refFolded(count: number): string {
  return `×${formatNumber(count)}`;
}

/** «в 3 разделах:» — различающиеся пути свёрнутых строк. */
export function refSections(count: number): string {
  return `в ${formatNumber(count)} ${pluralForm(count, "разделе", "разделах", "разделах")}:`;
}

export const REF_LINK_KIND_TEXT: Record<"thickness" | "width" | "density", string> = {
  thickness: "через толщину",
  width: "через ширину",
  density: "через плотность",
};

/** Заголовок блока Г (спека §2.9): «Справочно: {семья} · {ед} · пересчёт …»; у постоянного — «1 {ед} = {N} {ед}». */
export function refLinkTitle(
  family: { title: string; unit: string | null },
  kind: "thickness" | "width" | "density" | "constant",
  constant: { factorShown: string; unit: string | null } | null
): string {
  const unit = family.unit === null ? "" : ` · ${family.unit}`;
  let through: string;
  if (kind === "constant") {
    through =
      constant === null
        ? "постоянный"
        : `постоянный: 1 ${family.unit ?? "ед."} = ${constant.factorShown} ${constant.unit ?? "ед."}`;
  } else {
    through = REF_LINK_KIND_TEXT[kind];
  }
  return `Справочно: ${family.title}${unit} · пересчёт ${through}`;
}

/** Пересчёт строки договора: «1 м² = 0,100 м³ — основание». */
export function refConversionText(row: Pick<RefRow, "unit" | "conversion">, kpUnit: string | null): string | null {
  const conversion = row.conversion;
  if (conversion === null) return null;
  return `1 ${row.unit ?? "ед."} = ${conversion.factor_shown} ${kpUnit ?? "ед."}`;
}

export function refDeviation(text: string): string {
  return `КП ${text.replace("%", " %")}`;
}

export const REF_PARAM_VALUE = "параметр";

export const REF_SEARCH = {
  title: "Найти в договорах",
  label: "Слова из наименования",
  hint: "ищите по корням слов",
  submit: "Найти",
  unitsAll: "все единицы",
  unitsKp: "как у строки КП",
  unitsLabel: "Единицы",
  empty: "Ничего не нашлось.",
  invalid: "Запрос не годится: от двух знаков, не больше шести слов.",
  failed: "Поиск не удался — повторите.",
  zoneUnknown: "зона не определена",
  linkConversion: "пересчёт по связи",
  manualNeeded: "нужен пересчёт: N вводит человек",
  loading: "Ищем…",
} as const;

export function refTruncated(limit: number): string {
  return `показаны первые ${formatNumber(limit)}, уточните запрос`;
}

export function refSearchFound(total: number): string {
  return `${formatNumber(total)} ${pluralForm(total, "строка", "строки", "строк")}`;
}

export const REF_DIALOG = {
  title: "Пересчёт в единицу строки КП",
  description: "Единицы строки договора и строки КП различны: цена пересчитывается во вторую.",
  nameLabel: "Множитель N",
  basisLabel: "Основание",
  basisPlaceholder: "Откуда взят множитель",
  linkNote: "Пересчёт по связи семей: N и основание только для просмотра.",
  manualNote: "Связи нет — N и основание вводит человек. Без основания пересчёт не сохраняется.",
  stale: "Пересчёт по связи изменился, пока диалог был открыт: проверьте новые N, основание и цену и нажмите ещё раз.",
  staleRefreshing:
    "Цена в единице строки КП обновляется. Если она не обновилась, закройте окно и выберите строку заново.",
  cancel: "Отмена",
  submit: "Сделать целью",
  saving: "Сохраняем…",
  priceInKp: "цена в единице строки КП",
  invalidManual: "N — число больше нуля (до шести знаков после запятой), основание — не пустое.",
} as const;

/** «Запомнить для семей» в диалоге пересчёта (спека справочных аналогов §2.18). */
export const REF_REMEMBER = {
  label: "Запомнить для семей",
  kind: "Вид связи",
  paramKp: "Параметр семьи строки КП",
  paramRow: "Параметр семьи строки договора",
  paramsNote: "Нужен параметр хотя бы одной стороны. Предзаполнено по названию, проверьте.",
  noFactor: "Связь заведена, но пересчёт по ней не получился.",
  saved: "Связь сохранена. Проверьте пересчёт и нажмите «Сделать целью».",
} as const;

export const REF_OUTLIER_CONFIRM = {
  title: "Строка помечена выбросом",
  confirm: "Всё равно сделать целью",
  cancel: "Отмена",
} as const;

export function refOutlierConfirmText(ratioNote: string): string {
  return `${ratioNote}. Выбрать её целью?`;
}

// ---------------------------------------------------------------------------
//  Карточка справочной цели (спека справочных аналогов §2.14, §2.15)
// ---------------------------------------------------------------------------

export const REF_TARGET = {
  title: "Цель строки",
  origin: {
    zone: "справочная · другая зона",
    search: "справочная · из поиска",
    link: "справочная · по связи",
  },
  sourcePrice: "в договоре",
  convertedPrice: "в единице строки КП",
  resetToLowest: "вернуть самую низкую",
  linkGone: "Связь удалена или не действует; цифра — по снимку.",
  ownAnalogs: "У строки появились свои аналоги.",
  rowGone: "Строка договора больше не доступна, цифры цели нет.",
} as const;

/** «пересчёт из м²» — единица строки договора, из которой цена пересчитана. */
export function refTargetConvertedFrom(unit: string | null): string {
  return `пересчёт из ${unit ?? "ед."}`;
}

/** Основание пересчёта: «1 м² = 0,1 м³ — толщина 100 мм». */
export function refTargetConversionLine(
  contractUnit: string | null,
  kpUnit: string | null,
  factorShown: string,
  basis: string
): string {
  return `1 ${contractUnit ?? "ед."} = ${factorShown} ${kpUnit ?? "ед."} — ${basis}`;
}

/** Подсказка `link_now`: связь сегодня даёт другой множитель, цифра цели прежняя. */
export function refTargetLinkNow(contractUnit: string | null, kpUnit: string | null, factorShown: string): string {
  return `По связи сейчас 1 ${contractUnit ?? "ед."} = ${factorShown} ${kpUnit ?? "ед."}; чтобы пересчитать — выберите цель заново.`;
}

export function refTargetInactive(reason: TargetResetReason | null): string {
  const why = reason === null ? "" : `: ${TARGET_RESET_REASON[reason]}`;
  return `Справочная цель не действует${why}`;
}

/**
 * Источник справочной цели в блоке «В файл подрядчику» (спека §2.15): договор, путь разделов, цитата
 * номинала в базе НДС КП и, при пересчёте, множитель с основанием и цена в единице КП.
 */
export function refExportSource(row: {
  object: string;
  number: string;
  year: number;
  path: string | null;
  quote: string | null;
  unit: string | null;
  vat: string;
  conversion: { factorShown: string; basis: string; kpUnit: string | null; converted: string | null } | null;
}): string {
  const unit = row.unit ?? "ед.";
  const path = row.path === null ? "" : ` (${row.path})`;
  const quote = row.quote === null ? "" : `, ${row.quote} ₽ за ${unit} ${row.vat}`;
  let text = `${row.object}, договор № ${row.number}, ${row.year} г.: сопоставимая работа${path}${quote}`;
  if (row.conversion !== null) {
    const kp = row.conversion.kpUnit ?? "ед.";
    const converted = row.conversion.converted === null ? "" : `: ${row.conversion.converted} ₽ за ${kp} ${row.vat}`;
    text += `; 1 ${unit} = ${row.conversion.factorShown} ${kp} (${row.conversion.basis})${converted}`;
  }
  return text;
}
