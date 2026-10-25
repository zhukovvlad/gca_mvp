import { compareDecimalStrings } from "@/lib/decimal";
import { formatDecimalMoney } from "@/lib/format";
import type { Decimal, MoneyPair } from "@/types/domain";

import { SUB_KOPECK_LABEL } from "./ourPriceCopy";

/** Вид денег на экране: нетто либо в ставке КП (`shown`). */
export type VatView = "net" | "shown";

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
  const text = formatDecimalMoney(value);
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
  return positive && underKopeck ? SUB_KOPECK_LABEL : formatDecimalMoney(value, "");
}
