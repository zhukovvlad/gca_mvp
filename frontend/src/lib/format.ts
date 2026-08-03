export function formatMoney(value: number | null | undefined, currency = "₽"): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${value.toLocaleString("ru-RU", { maximumFractionDigits: 2 })} ${currency}`;
}

/** Неразрывный пробел — им `ru-RU` группирует разряды. */
const NBSP = " ";

/** Десятичное число в виде строки: `-?цифры[.цифры]`. */
const DECIMAL_RE = /^(-?)(\d+)(?:\.(\d+))?$/;

/**
 * Форматирует денежную строку, НЕ переводя её в число.
 *
 * Деньги приходят из API строками — это требование AGENTS.md §3 («numeric в БД ↔
 * Decimal в Python ↔ строки в JSON»), и `Number(value)` свёл бы его на нет на
 * последнем шаге: double несёт ~15–16 значащих цифр, а суммы по договорам ГП
 * вполне доходят до десятка цифр до запятой плюс копейки. Ошибка была бы
 * невидимой — в последнем разряде.
 *
 * Дробная часть НЕ округляется и не обрезается: показать «1 075,35» вместо
 * утверждённой ставки 1 075,35475 значило бы соврать о цифре, по которой идёт
 * торг. Меньше двух знаков — дополняется нулями по денежной привычке.
 *
 * @param value десятичная строка либо число (число приводится через String, без
 *   промежуточного форматирования), либо `null`.
 * @param currency знак валюты; пустая строка — без него.
 */
export function formatDecimalMoney(
  value: string | number | null | undefined,
  currency = "₽"
): string {
  if (value === null || value === undefined) return "—";

  const raw = String(value).trim();
  if (!raw) return "—";

  const parsed = DECIMAL_RE.exec(raw);
  // Неожиданный формат отдаём как есть: молча превратить его в «—» значило бы
  // спрятать данные, которые пришли с сервера.
  if (!parsed) return currency ? `${raw}${NBSP}${currency}` : raw;

  const [, sign, whole, fraction = ""] = parsed;
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, NBSP);
  const decimals = fraction.length >= 2 ? fraction : fraction.padEnd(2, "0");
  const amount = `${sign}${grouped},${decimals}`;
  return currency ? `${amount}${NBSP}${currency}` : amount;
}

export function formatPercent(value: number | null | undefined, withSign = false): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const sign = withSign && value > 0 ? "+" : "";
  return `${sign}${value.toFixed(1)}%`;
}

export function formatNumber(value: number | null | undefined, fractionDigits?: number): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (fractionDigits !== undefined) {
    return value.toLocaleString("ru-RU", { minimumFractionDigits: fractionDigits, maximumFractionDigits: fractionDigits });
  }
  return value.toLocaleString("ru-RU");
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  });
}

export function formatRelative(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";

  const now = Date.now();
  const diffMs = now - d.getTime();
  const diffMin = Math.round(diffMs / 60_000);
  const diffHr = Math.round(diffMs / 3_600_000);
  const diffDay = Math.round(diffMs / 86_400_000);

  if (diffMin < 1) return "только что";
  if (diffMin < 60) return `${diffMin} мин назад`;
  if (diffHr < 24) return `${diffHr} ч назад`;
  if (diffDay < 7) return `${diffDay} дн назад`;
  return formatDate(iso);
}

/** Русские окончания для слова с числом (поставщик, объект и т.п.).
 *  Возвращает суффикс: "" / "а" / "ов". */
export function pluralRu(n: number): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return "";
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return "а";
  return "ов";
}
