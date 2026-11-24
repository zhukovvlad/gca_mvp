import { formatDate, pluralRu } from "@/lib/format";
import { formatMoney } from "@/pages/tenders/our-price/money";
import type {
  BasisView,
  FactView,
  NotErrorDecision,
  PriceNote,
  SuspiciousLineHead,
} from "@/types/domain";

/**
 * Тексты экрана «Подозрительные строки» (спека 2026-11-24-suspicious-lines-design.md, раздел 5 п. 6;
 * макет рядом с дизайном, разделы 1, 2, 4). Подсказка панели строки КП берёт их отсюда же.
 */

export const SUSPICIOUS_TAB_CONTRACTS = "Договоры";
export function suspiciousTabLabel(total: number | undefined): string {
  return total === undefined ? "Подозрительные строки" : `Подозрительные строки (${total})`;
}

export const SUSPICIOUS_FILTER_LABEL = "Договор";
export const SUSPICIOUS_FILTER_ALL = "Все договоры";

export const KIND_OUTLIER = "↕ цена выбивается";
export const KIND_TWIN = "= та же цена, другая единица";

export const SUMMARY_UNRESOLVED = "не разобраны";
export const SUMMARY_RESOLVED = "разобраны";

export function unresolvedTitle(count: number): string {
  return `Не разобраны · ${count}`;
}
export function resolvedTitle(count: number): string {
  return `Разобраны (${count})`;
}
export const RESOLVED_HINT =
  "Все решения по ценам строк договоров — и те, что правило сейчас не ловит: такая строка остаётся здесь с пометкой «правило сейчас её не ловит».";
export const EMPTY_UNRESOLVED = "Подозрительных строк нет.";
export const LOAD_ERROR = "Не удалось получить список подозрительных строк.";
export const SUSPICION_CHANGED_NOTICE = "Подозрение по строке изменилось — проверьте заново";

export const BLOCK_NEIGHBORS = "Строки рядом в смете";
export const BLOCK_PRICES = "Цены этой работы в договорах";

export const NEIGHBOR_HEADERS = {
  number: "п.",
  title: "Наименование",
  unit: "Ед.",
  quantity: "Кол-во",
  price: "Цена за ед.",
  materials: "Материалы за ед.",
  works: "Работы за ед.",
  total: "Сумма",
} as const;
export const NEIGHBORS_NOTE =
  "По 3 строки сметы до и после, без заголовков разделов; цены без НДС; сумма — как в смете.";
export const NEIGHBORS_NOTE_GROSS =
  "По 3 строки сметы до и после, без заголовков разделов. НДС сметы не известен — цены и суммы как в смете.";
export const NEIGHBOR_SELF = "эта строка";
export const NEIGHBOR_TWIN = "соседняя строка";

export const PRICES_HEADERS = {
  contract: "Договор",
  lines: "Строк",
  price: "Цена договора",
  range: "От — до",
} as const;
export function pricesNote(family: string | null, unit: string | null, usual: string): string {
  const name = family === null ? "Работа" : `«${family}»`;
  const per = unit === null ? "" : `, за ${unit}`;
  return `${name}${per}, без НДС. Цена договора — середина его строк этой работы; обычная цена ${formatMoney(usual)} — середина цен договоров (каждый договор — один голос, сколько бы строк в нём ни было).`;
}

export const ACTION_PRICE_ERROR = "Ошибка в цене…";
export const ACTION_NOT_ERROR = "Не ошибка…";
export const NOT_ERROR_TITLE = "Не ошибка";
export const NOT_ERROR_SUBMIT = "Сохранить";
export const NOT_ERROR_CLEAR = "Вернуть";
export const RESOLVED_NOT_ERROR = "не ошибка";
export const RULE_NOT_CATCHING = "правило сейчас её не ловит";
export const PREVIOUS_LABEL = "Решения на другие основания";
export const LINE_OLD_ESTIMATE = "строка прежней сметы";

export const STRIP_LABEL = "цены договоров и эта строка";
export const STRIP_THIS = "эта строка";
export const STRIP_USUAL = "обычная";
export const STRIP_LEGEND = {
  contract: "цена договора",
  own: "этот же договор",
  usual: "обычная — середина договоров",
  line: "эта строка",
  band: "разброс других договоров",
} as const;
export const STRIP_NOMINAL_NOTE = "Цены подсказки — номинальные, без НДС, без индексации.";

export const CARD_LINK_NOTE = "не разобраны";
export function cardLinkLabel(count: number): string {
  return `Подозрительные строки: ${count} →`;
}

/** «×10,6» у выброса вверх, «÷88» вниз: множитель считает фронт из `ratio`, это не деньги. */
export function factorText(ratio: string, direction: "up" | "down"): string {
  const value = Number(ratio);
  if (!Number.isFinite(value) || value <= 0) return "";
  const factor = direction === "up" ? value : 1 / value;
  const digits = factor >= 100 ? 0 : 1;
  const text = factor.toLocaleString("ru-RU", { maximumFractionDigits: digits });
  return `${direction === "up" ? "×" : "÷"}${text}`;
}
export function factorCaption(direction: "up" | "down"): string {
  return direction === "up" ? "дороже обычного" : "дешевле обычного";
}
/** «в 10,6 раза дороже», «в 88 раз дешевле» — без «×»: для предложения-факта. */
export function factorWords(ratio: string, direction: "up" | "down"): string {
  const value = Number(ratio);
  const factor = direction === "up" ? value : 1 / value;
  const rounded = factor >= 100 ? Math.round(factor) : Math.round(factor * 10) / 10;
  const few = Number.isInteger(rounded)
    ? [2, 3, 4].includes(rounded % 10) && ![12, 13, 14].includes(rounded % 100)
    : true;
  const text = rounded.toLocaleString("ru-RU", { maximumFractionDigits: 1 });
  return `в ${text} ${few ? "раза" : "раз"} ${direction === "up" ? "дороже" : "дешевле"}`;
}

function perUnit(unit: string | null): string {
  return unit === null ? "" : ` за ${unit}`;
}

export function contractsWord(count: number): string {
  return `${count} договор${pluralRu(count)}`;
}

/** Цена строки в шапке пункта: «₽ за м² без НДС» (спека, раздел 5 п. 1). */
export function priceSuffix(unit: string | null): string {
  return `₽${perUnit(unit)} без НДС`;
}

/** Подпись цены отмеченной строки без нетто (спека, раздел 4 п. 2, таблица `price_note`). */
export function priceNoteText(note: PriceNote, head: Pick<SuspiciousLineHead, "raw">): string {
  switch (note) {
    case "no_price":
      return "цены нет";
    case "not_finite":
      return `в смете: ${head.raw ?? ""} — не число`;
    case "negative":
      return `в смете: ${head.raw ?? ""}`;
    case "vat_unknown":
      return "НДС сметы не известен — цена как в смете";
  }
}

/** Основание решения или факта словами: «выброс в «Дверях», ×19,6 дороже». */
export function basisText(basis: BasisView, fact?: Extract<FactView, { kind: "outlier" }>): string {
  if (basis.kind === "outlier") {
    const family = basis.family_title === null ? "семье" : `«${basis.family_title}»`;
    const tail = fact === undefined ? (basis.direction === "up" ? ", дороже обычного" : ", дешевле обычного") : `, ${factorText(fact.ratio, fact.direction)} ${factorCaption(fact.direction)}`;
    return `выброс в ${family}${tail}`;
  }
  const number = basis.neighbor.item_number === null ? "" : `п. ${basis.neighbor.item_number} `;
  const title = basis.neighbor.title === null ? "" : `«${basis.neighbor.title}»`;
  return `та же цена, что у соседней строки ${number}${title}`.trim() + ", другая единица";
}

/** Основание показанного факта: для текста окна «Не ошибка» (макет, раздел 4). */
export function factBasisText(fact: FactView, unitOfLine: string | null): string {
  if (fact.kind === "outlier") {
    const family = fact.family_title === null ? "семье" : `«${fact.family_title}»`;
    return `выброс в ${family}, ${factorText(fact.ratio, fact.direction)} ${factorCaption(fact.direction)}`;
  }
  const n = fact.neighbor;
  const number = n.item_number === null ? "" : `п. ${n.item_number} `;
  return `та же цена, что у соседней строки ${number}«${n.title}» (${n.unit ?? "—"} и ${unitOfLine ?? "—"})`;
}

/** Факт правила предложением, цифры — те же, что у подсказки панели (спека, раздел 7 п. 7). */
export function factSentence(fact: FactView, line: { unit: string | null; net: string | null }): string {
  if (fact.kind === "outlier") {
    const family = fact.family_title === null ? "" : ` «${fact.family_title}»`;
    const own = line.net === null ? "" : ` Эта строка — ${formatMoney(line.net)}${perUnit(fact.unit)}.`;
    return `Обычная цена${family} — ${formatMoney(fact.usual)}${perUnit(fact.unit)}: середина цен ${contractsWord(fact.contracts)}.${own}`;
  }
  const n = fact.neighbor;
  const number = n.item_number === null ? "" : ` п. ${n.item_number}`;
  return `Та же цена, что у соседней строки${number} «${n.title}» — но там за ${n.unit ?? "—"}, а здесь за ${line.unit ?? "—"}.`;
}

/** Текст окна «Не ошибка» (макет, раздел 4): что уйдёт из подозрительных и что останется. */
export function notErrorScopeText(
  row: { title: string; objectTitle: string },
  facts: readonly FactView[],
  unit: string | null
): string {
  const bases = facts.map((fact) => factBasisText(fact, unit)).join("; ");
  return `Строка «${row.title}» договора ${row.objectTitle} уйдёт из подозрительных по проверенному основанию — ${bases} — и перестанет показывать подсказку. Если строку поймает другое правило или она окажется в другой семье, она вернётся в «Не разобраны». Аналогом она остаётся, как сейчас. Решение можно вернуть.`;
}

/** «Проверено: выброс в «Дверях», ×19,6 дороже обычного» и подпись «причина · автор · дата». */
export function decisionByline(decision: NotErrorDecision): string {
  return `«${decision.reason}» · ${decision.set_by.email} · ${formatDate(decision.set_at)}`;
}

export function previousText(decision: NotErrorDecision): string {
  return `${basisText(decision.basis)}`;
}

export const HINT_TITLE = "Подозрительная цена";
export const HINT_TWIN_TITLE = "та же цена, другая единица";
export const HINT_LINK = "Разобрать в списке ↗";
export const HINT_LINK_NOTE = "новая вкладка: строки рядом в смете и цены по договорам; панель КП остаётся открытой";

/** Адрес строки в списке подозрительных (вкладка администратора, пункт прокручивается и раскрывается). */
export function hintListHref(analogId: number): string {
  return `/contracts?tab=suspicious&line=${analogId}`;
}

/** Предложение подсказки панели (макет, раздел 3): «в 10,6 раза дороже обычного: эта строка … ₽ за м², обычная цена … — … ₽, середина цен 7 договоров». Цифры — только из `suspicion`. */
export function hintSentence(fact: FactView, line: { unit: string | null; net: string | null }): string {
  if (fact.kind === "twin") return factSentence(fact, line);
  const family = fact.family_title === null ? "обычная цена" : `обычная цена «${fact.family_title}»`;
  const own = line.net === null ? "эта строка" : `эта строка ${formatMoney(line.net)}${perUnit(fact.unit)}`;
  return `${factorWords(fact.ratio, fact.direction)} обычного: ${own}, ${family} — ${formatMoney(fact.usual)}, середина цен ${contractsWord(fact.contracts)}.`;
}

/** Серая строчка панели: «проверено, не ошибка — {основание}: «{причина}» · {автор} · {дата}». */
export function checkedLine(decision: NotErrorDecision): string {
  return `проверено, не ошибка — ${basisText(decision.basis)}: ${decisionByline(decision)}`;
}
