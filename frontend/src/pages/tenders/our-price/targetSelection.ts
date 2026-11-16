import type { OurPriceLine } from "@/types/domain";

/** Стратегия цели: вид выбора, который сохраняется на сервере, а не конкретный договор. */
export type TargetStrategy = "lowest" | "freshest" | "contractor";

/**
 * Что сейчас выбрано целью: стратегия, закреплённый аналог либо `null` — цели нет (строка не сравнима).
 * «Договорились» цель не меняет, а перекрывает: выбор считается по сохранённому выбору человека.
 */
export function selectedTarget(line: OurPriceLine): TargetStrategy | "analog" | "reference" | null {
  if (line.target_kind === null) return null;
  const choice = line.choice;
  return choice !== null && choice.active ? choice.kind : "lowest";
}

/** Аналог, на который цель указывает сейчас (по стратегии или закреплённый); `null` — не указывает ни на какой. */
export function targetAnalogId(line: OurPriceLine): number | null {
  const selected = selectedTarget(line);
  if (selected === "lowest") return line.lowest?.analog_id ?? null;
  if (selected === "freshest") return line.freshest?.analog_id ?? null;
  if (selected === "contractor") return line.contractor?.analog_id ?? null;
  if (selected === "analog" || selected === "reference") return line.choice?.analog_id ?? null;
  return null;
}
