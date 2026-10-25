import { formatDecimalMoney } from "@/lib/format";
import type { Decimal, MoneyPair } from "@/types/domain";

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
