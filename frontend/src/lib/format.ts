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
 * Значащие цифры НЕ округляются: показать «1 075,35» вместо утверждённой ставки
 * 1 075,35475 значило бы соврать о цифре, по которой идёт торг. Меньше двух знаков
 * — дополняется нулями по денежной привычке.
 *
 * **Хвостовые нули за пределами копеек отбрасываются.** Это не округление и ничего
 * не теряет: `32 263 577,210000000000` и `32 263 577,21` — одно и то же число.
 * Нужно потому, что деление `numeric` в PostgreSQL доводит результат до своей
 * шкалы, и средневзвешенная ставка §6 приезжает с десятком нулей на конце. На
 * стенде матрица из-за этого читалась как набор случайных цифр.
 *
 * @param value десятичная строка либо число (число приводится через String, без
 *   промежуточного форматирования), либо `null`.
 * @param currency знак валюты; пустая строка — без него.
 * @param maxFractionDigits если задано — значение округляется до этого числа знаков
 *   целочисленной арифметикой. Задавать **только для вычисленных** величин
 *   (средневзвешенная ставка), где лишние знаки — артефакт деления, а не данные.
 *   Для хранимых ставок и сумм не задавать.
 */
export function formatDecimalMoney(
  value: string | number | null | undefined,
  currency = "₽",
  maxFractionDigits?: number
): string {
  if (value === null || value === undefined) return "—";

  const raw = String(value).trim();
  if (!raw) return "—";

  const source = maxFractionDigits === undefined ? raw : roundDecimal(raw, maxFractionDigits);

  const parsed = DECIMAL_RE.exec(source);
  // Неожиданный формат отдаём как есть: молча превратить его в «—» значило бы
  // спрятать данные, которые пришли с сервера.
  if (!parsed) return currency ? `${source}${NBSP}${currency}` : source;

  const [, sign, whole, fraction = ""] = parsed;
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, NBSP);
  // Копейки показываем всегда; всё, что дальше них, — только если это не нули.
  const trimmed = fraction.length > 2 ? fraction.replace(/0+$/, "") : fraction;
  const decimals = trimmed.length >= 2 ? trimmed : trimmed.padEnd(2, "0");
  const amount = `${sign}${grouped},${decimals}`;
  return currency ? `${amount}${NBSP}${currency}` : amount;
}

/**
 * Округляет десятичную строку до `digits` знаков **целочисленной арифметикой**.
 *
 * Тот же приём и та же причина, что у `roundDecimalPercent`: `Number()` для денег
 * запрещён §3, и обходить запрет «только для показа» нельзя — двоичное
 * представление решало бы исход на границе округления.
 *
 * Неразбираемый вход возвращается как есть: решение о том, показывать ли его,
 * принимает вызывающий, а не эта функция.
 */
export function roundDecimal(value: string, digits: number): string {
  const parsed = DECIMAL_RE.exec(value);
  if (!parsed) return value;

  const [, sign, whole, fraction = ""] = parsed;
  if (fraction.length <= digits) return value;

  const scale = digits + 1;
  const padded = (fraction + "0".repeat(scale)).slice(0, scale);
  const rounded = (BigInt(`${whole}${padded}`) + 5n) / 10n;

  const unit = 10n ** BigInt(digits);
  const intPart = (rounded / unit).toString();
  const fracPart = digits > 0 ? (rounded % unit).toString().padStart(digits, "0") : "";
  // Знак минуса у нуля не бывает: «-0,00» — артефакт округления.
  const negative = sign === "-" && rounded !== 0n;
  return fracPart ? `${negative ? "-" : ""}${intPart}.${fracPart}` : `${negative ? "-" : ""}${intPart}`;
}

/**
 * Доля статьи: decimal-строка → «N,NN %», **ровно два знака** после запятой.
 *
 * Один помощник на оба места показа доли в паспорте — таблицу по статьям и
 * легенду кольца. В проекте есть сознательное правило держать трёхстрочные копии
 * по месту использования (`isZeroDecimal` живёт тремя экземплярами), и здесь оно
 * отменено не вкусом, а замером: копий было две, и правка одной их развела —
 * добивание до двух знаков появилось в таблице, а в легенде пробел нашло внешнее
 * ревью (спека Ф6a §1.3, §2.3).
 *
 * Добивание нужно потому, что `roundDecimal` его не делает: его контракт — «не
 * БОЛЬШЕ `digits` знаков», и вход короче возвращается как есть. Живой путь —
 * сумма долей «Остальных статей»: `addDecimalStrings` срезает хвостовые нули, и
 * 1,10 приезжает строкой «1.1», то есть печаталось «1,1 %» рядом с «12,72 %».
 *
 * Разряды не группируются: доля лежит в 0…100, группировать нечего.
 *
 * Обработка `null` **остаётся на месте вызова** и сюда не втягивается: у таблицы
 * это прочерк в колонке, у легенды — опустить процент вовсе (правило 5 §2.10
 * спеки Ф6). Общим стало ровно то, что у них общее.
 */
export function formatSharePercent(value: string): string {
  const rounded = roundDecimal(value.trim(), 2);

  const parsed = DECIMAL_RE.exec(rounded);
  // Неожиданный формат отдаём как есть — то же правило, что у
  // `formatDecimalMoney`: молча превратить пришедшее с сервера в «0,00 %»
  // значило бы соврать о нуле.
  if (!parsed) return `${rounded} %`;

  const [, sign, whole, fraction = ""] = parsed;
  return `${sign}${whole},${fraction.padEnd(2, "0")} %`;
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
