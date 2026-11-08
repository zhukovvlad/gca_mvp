import { compareDecimalStrings } from "@/lib/decimal";
import type { OurPriceLine } from "@/types/domain";

/** Фильтр строк разбора; плитки разбора — его переключатели (спека 4а §2.14). */
export type OfferFilter =
  | "all"
  | "above_lowest"
  | "above_freshest"
  | "below_lowest"
  | "unit_mismatch"
  | "not_comparable";

/**
 * Состав фильтра задаёт состояние строки, а не пустота полей: у `unit_mismatch`
 * `over_lowest` и `below` по контракту пусты, но и заполненными они в «выше» и «ниже»
 * не входят — разница от единицы учёта, а не от цены (спека 4а §2.8). «Выше самых свежих» —
 * только у строки с самой свежей расценкой: без неё `over_freshest = null`, не ноль.
 */
export function matchesFilter(line: OurPriceLine, filter: OfferFilter): boolean {
  switch (filter) {
    case "all":
      return true;
    case "above_lowest":
      return (
        line.state === "comparable" &&
        line.over_lowest !== null &&
        compareDecimalStrings(line.over_lowest.net, "0") === 1
      );
    case "above_freshest":
      return (
        line.state === "comparable" &&
        line.over_freshest !== null &&
        compareDecimalStrings(line.over_freshest.net, "0") === 1
      );
    case "below_lowest":
      return line.state === "comparable" && line.below !== null;
    case "unit_mismatch":
      return line.state === "unit_mismatch";
    case "not_comparable":
      return line.state !== "comparable" && line.state !== "unit_mismatch";
  }
}
