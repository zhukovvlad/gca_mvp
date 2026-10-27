import { compareDecimalStrings } from "@/lib/decimal";
import type { OurPriceLine } from "@/types/domain";

/** Фильтр строк разбора; плитки разбора — его переключатели (спека 4а §2.14). */
export type OfferFilter = "all" | "above_ours" | "below_paid" | "unit_mismatch" | "not_comparable";

/**
 * Состав фильтра задаёт состояние строки, а не пустота полей: у `unit_mismatch`
 * `bargain` и `below` по контракту пусты, но и заполненными они в «дороже» и «ниже»
 * не входят — разница от единицы учёта, а не от цены (спека 4а §2.8).
 */
export function matchesFilter(line: OurPriceLine, filter: OfferFilter): boolean {
  switch (filter) {
    case "all":
      return true;
    case "above_ours":
      return (
        line.state === "comparable" &&
        line.bargain !== null &&
        compareDecimalStrings(line.bargain.net, "0") === 1
      );
    case "below_paid":
      return line.state === "comparable" && line.below !== null;
    case "unit_mismatch":
      return line.state === "unit_mismatch";
    case "not_comparable":
      return line.state !== "comparable" && line.state !== "unit_mismatch";
  }
}
