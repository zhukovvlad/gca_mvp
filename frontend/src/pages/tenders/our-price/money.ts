import { compareDecimalStrings } from "@/lib/decimal";
import { formatDecimalMoney, roundDecimal } from "@/lib/format";
import type { Decimal, MoneyPair, OurPriceLine } from "@/types/domain";

import { PER_SQM_UNIT, SUB_KOPECK_LABEL } from "./ourPriceCopy";

/** Вид денег на экране: нетто либо в ставке КП (`shown`). */
export type VatView = "net" | "shown";

/** Знаков после запятой у денег на экране: сервер отдаёт ставки и ₽/м² без округления (спека 4а §2.9). */
const MONEY_DIGITS = 2;

/**
 * Деньги на экране: не больше двух знаков после запятой, округление строкой (половина вверх).
 * Единственный путь печати денежного значения на экранах «Против нашей цены»: сравнения,
 * цвета и сортировка идут по полным строкам сервера, а не по тому, что напечатано.
 */
export function formatMoney(value: Decimal | null | undefined, currency = "₽"): string {
  return formatDecimalMoney(value, currency, MONEY_DIGITS);
}

/** Знаков после запятой у объёма: как формат `#,##0.###` в книге файла подрядчику. */
const QUANTITY_DIGITS = 3;

/**
 * Объём строки на экране: до трёх знаков после запятой, без хвостовых нулей, разряды неразрывным
 * пробелом, запятая. Это печать книги (`#,##0.###`), а не денег: «0.001» не должно стать «0,00».
 * Округление строкой (половина вверх), `Number` не участвует; неразбираемое отдаётся как есть.
 */
export function formatQuantity(value: Decimal): string {
  const rounded = roundDecimal(value.trim(), QUANTITY_DIGITS);
  const parsed = /^(-?)(\d+)(?:\.(\d+))?$/.exec(rounded);
  if (!parsed) return rounded;
  const [, sign, whole, fraction = ""] = parsed;
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, String.fromCharCode(160));
  const tail = fraction.replace(/0+$/, "");
  const zero = /^0*$/.test(whole) && tail === "";
  return `${zero ? "" : sign}${grouped}${tail === "" ? "" : `,${tail}`}`;
}

/**
 * Выбирает готовое поле пары — арифметики над деньгами на клиенте нет: нетто и
 * «в ставке КП» посчитал сервер (спека 4а §2.9). `null` — пары нет или база НДС
 * неизвестна (`shown = null`).
 */
export function pick(pair: MoneyPair | null, view: VatView): Decimal | null {
  if (pair === null) return null;
  return view === "net" ? pair.net : pair.shown;
}

/**
 * Подписанная разница рублями: знак печатается явно («+» / «−»), ноль без знака.
 * Знак читается по строке, арифметики нет.
 */
export function formatSigned(value: Decimal): string {
  const text = formatMoney(value);
  if (text.startsWith("-")) return `−${text.slice(1)}`;
  return /^0([.,]0+)?\s/.test(text) ? text : `+${text}`;
}

/**
 * Сумма «ниже всего, что платили» со знаком «−»: та же типографика, что у «дороже».
 * Сервер отдаёт её отрицательной; знак берётся от модуля, поэтому функция не зависит
 * от знака входа. Ноль знака не получает. Арифметики нет.
 */
export function formatBelow(value: Decimal): string {
  const magnitude = absDecimal(value);
  return compareDecimalStrings(magnitude, "0") === 0 ? formatSigned(magnitude) : formatSigned(`-${magnitude}`);
}

/** Модуль подписанной суммы: строка без ведущего «-», арифметики нет. */
export function absDecimal(value: Decimal): Decimal {
  return value.startsWith("-") ? value.slice(1) : value;
}

/**
 * Цена за единицу без знака валюты. Положительная цена меньше копейки печатается
 * «< 0,01»: «0,00» читался бы как «цены нет». Сравнение — по строке, арифметики нет.
 */
export function formatUnitPrice(value: Decimal): string {
  const positive = compareDecimalStrings(value, "0") === 1;
  const underKopeck = compareDecimalStrings(value, "0.01") === -1;
  return positive && underKopeck ? SUB_KOPECK_LABEL : formatMoney(value, "");
}

/** Подпись цены за единицу измерения строки: «₽/м3»; без единицы — просто «₽». */
export function priceUnitLabel(unit: string | null): string {
  return unit === null ? "₽" : `₽/${unit}`;
}

/**
 * Единица сравниваемой цены КП строки. У системы цена за комплекс с вилкой договоров в ₽/м² глазом
 * не сравнивается, поэтому сравнивается цена группы за м² объекта (спека 4а §2.8).
 */
export function comparedUnit(line: OurPriceLine): string {
  return line.kind === "system" ? PER_SQM_UNIT : priceUnitLabel(line.unit);
}

/** Сравниваемая цена КП строки в выбранном виде: у системы — цена группы за м², без группы — `null`. */
export function comparedKpPrice(line: OurPriceLine, vat: VatView): Decimal | null {
  return line.kind === "system"
    ? pick(line.group?.kp_per_sqm ?? null, vat)
    : pick(line.kp.unit_price, vat);
}
