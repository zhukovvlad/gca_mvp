import type { FamilySchemaParameter, FamilySchemaValue } from "@/types/domain";

/**
 * Тексты частей составного значения (спека упрощения каталога §2.3, место 4): части — значения того
 * же параметра, их тексты берутся из его списка. `null` — значение простое. Часть, которой в списке
 * нет, пропускается: пометка не должна падать из-за устаревшего списка.
 */
export function compositePartTexts(
  parameter: FamilySchemaParameter,
  value: FamilySchemaValue
): string[] | null {
  if (value.composite === null) return null;
  return value.composite.part_ids.flatMap((id) => {
    const part = parameter.values.find((candidate) => candidate.id === id);
    return part === undefined ? [] : [part.value];
  });
}
