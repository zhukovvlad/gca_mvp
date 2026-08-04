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

/** Округлённое отклонение: текст для показа и знак ПОСЛЕ округления. */
export interface RoundedPercent {
  text: string;
  /** Знак округлённого значения: по нему выбирается цвет, а не по исходному. */
  sign: -1 | 0 | 1;
}

/**
 * Округляет отклонение до `digits` знаков **целочисленной арифметикой**, без float.
 *
 * `deviation_pct` — это `numeric` из VIEW, и §4 требует «точный Decimal, округление
 * до 0.1 п.п. только на слое представления». Поэтому значение приезжает строкой, и
 * `Number(value)` свёл бы требование на нет ровно на последнем шаге — так же, как
 * `Number()` для денег, на чём фаза 5 уже обожглась (`formatDecimalMoney`).
 *
 * Возражение «у процентов мало значащих цифр, double справится» верно по величине
 * и неверно по существу: `(ставка / норматив - 1) * 100` на `numeric` даёт больше
 * пятнадцати знаков, и у значения на границе округления (`x.x5`) двоичное
 * представление решает исход в произвольную сторону. Цена целочисленного пути —
 * пятнадцать строк; цена float — «система округляет не туда» в разговоре с
 * подрядчиком, где как раз и смотрят на десятую долю процента.
 *
 * Знак возвращается **после** округления: у отклонения +0,04 % текст «0,0 %», и
 * покрасить его как превышение значило бы противоречить показанной цифре.
 *
 * @returns `null`, если значения нет; для неразбираемого входа — сам вход со знаком 0
 *   (молча превратить его в «—» значило бы спрятать пришедшее с сервера).
 */
export function roundDecimalPercent(
  value: string | number | null | undefined,
  digits = 1
): RoundedPercent | null {
  if (value === null || value === undefined) return null;

  const raw = String(value).trim();
  if (!raw) return null;

  const parsed = DECIMAL_RE.exec(raw);
  if (!parsed) return { text: raw, sign: 0 };

  const [, sign, whole, fraction = ""] = parsed;

  // Масштабируем величину до digits+1 знаков и округляем целыми: половина — вверх
  // по модулю (знак вынесен отдельно), то есть привычное «от нуля».
  const scale = digits + 1;
  const padded = (fraction + "0".repeat(scale)).slice(0, scale);
  const rounded = (BigInt(`${whole}${padded}`) + 5n) / 10n;

  const unit = 10n ** BigInt(digits);
  const intPart = rounded / unit;
  const fracPart = rounded % unit;
  const magnitude = digits > 0
    ? `${intPart},${fracPart.toString().padStart(digits, "0")}`
    : `${intPart}`;

  // Ноль не бывает ни отрицательным, ни «со знаком плюс»: «-0,0 %» — артефакт.
  if (rounded === 0n) return { text: `0${digits > 0 ? `,${"0".repeat(digits)}` : ""}%`, sign: 0 };

  const negative = sign === "-";
  return {
    text: `${negative ? "-" : "+"}${magnitude}%`,
    sign: negative ? -1 : 1,
  };
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
