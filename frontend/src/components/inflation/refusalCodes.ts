export const MISSING_YEARS_CODE = "missing_inflation_years";
export const AMENDMENT_DATE_CODE = "amendment_date_missing";
/**
 * Этап без даты проведения и КП без даты подготовки: целевой месяц приведения
 * не от чего взять (спека 4а §2.10). Кнопки нет: правка ряда это не лечит.
 */
export const TARGET_MISSING_CODE = "inflation_target_missing";

/** Три отказа приведения: любой из них ведёт к номинальным цифрам и баннеру отказа. */
export const INFLATION_REFUSAL_CODES: readonly string[] = [
  MISSING_YEARS_CODE,
  AMENDMENT_DATE_CODE,
  TARGET_MISSING_CODE,
];

export function isInflationRefusal(code: string | undefined): boolean {
  return code !== undefined && INFLATION_REFUSAL_CODES.includes(code);
}
