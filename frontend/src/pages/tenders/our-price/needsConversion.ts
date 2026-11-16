import type { RefRow } from "@/types/domain";

/**
 * Единицы строки договора и строки КП различны (или одна пуста): цель требует пересчёта. Так же читает
 * сервер: единицы равны, только когда обе есть и совпали (спека справочных аналогов §2.13).
 */
export function needsConversion(row: Pick<RefRow, "unit">, kpUnit: string | null): boolean {
  return !(row.unit !== null && row.unit === kpUnit);
}
