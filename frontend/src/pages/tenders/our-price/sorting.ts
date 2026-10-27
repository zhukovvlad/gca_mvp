import { compareDecimalStrings } from "@/lib/decimal";
import type { Decimal } from "@/types/domain";

export type SortDir = "asc" | "desc";

export interface SortState<K> {
  key: K;
  dir: SortDir;
}

/** Щелчок по заголовку: активная колонка меняет направление, другая — по возрастанию. */
export function nextSort<K>(prev: SortState<K>, key: K): SortState<K> {
  return prev.key === key ? { key, dir: prev.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" };
}

export function ariaSort<K>(sort: SortState<K>, key: K): "ascending" | "descending" | "none" {
  if (sort.key !== key) return "none";
  return sort.dir === "asc" ? "ascending" : "descending";
}

/**
 * Десятичные строки сравниваются без перевода в число; пустые всегда в конце — в обоих
 * направлениях сортировки; две пустые равны.
 */
export function compareNullable(a: Decimal | null, b: Decimal | null, dir: SortDir): number {
  if (a === null) return b === null ? 0 : 1;
  if (b === null) return -1;
  const result = compareDecimalStrings(a, b) ?? 0;
  return dir === "asc" ? result : -result;
}
