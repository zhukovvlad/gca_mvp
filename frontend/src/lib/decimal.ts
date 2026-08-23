/** Десятичное число в виде строки: `-?цифры[.цифры]`. */
const DECIMAL_RE = /^(-?)(\d+)(?:\.(\d+))?$/;

/**
 * Приводит введённое человеком число к виду, который принимает сервер.
 *
 * Делает ровно две вещи, обе без потери точности:
 *
 * * убирает пробелы, включая неразрывный — ими группируют разряды, и «1 234.56»
 *   человек наберёт с них;
 * * заменяет запятую на точку, **только если точки нет**. Русская раскладка даёт
 *   «1234,56» естественнее, чем «1234.56», а сервер по §3 ждёт десятичную строку
 *   с точкой и на запятую отвечает ошибкой валидации Pydantic — по-английски и
 *   не о том.
 *
 * Условие «только если точки нет» существенно: в «1,234.56» запятая — разделитель
 * разрядов, и слепая замена дала бы «1.234.56», то есть мусор. Такую строку
 * оставляем как есть и отдаём серверу — пусть отказывает он, а не мы молча
 * искажаем введённое.
 */
export function normalizeDecimalInput(raw: string): string {
  // \u00A0 — неразрывный пробел: экранирован, а не вписан литералом, иначе в
  // исходнике стоял бы невидимый символ (eslint справедливо на это ругается).
  const compact = raw.replace(/[\s\u00A0]/g, "");
  return compact.includes(".") ? compact : compact.replace(",", ".");
}

/**
 * Умножает две десятичные строки **точно**, без числа с плавающей точкой.
 *
 * Нужно предзаполнению переутверждения норматива: новая ставка = прежняя × индекс
 * (§7.3). AGENTS.md §3 запрещает float для денег, и предзаполнение не исключение.
 * Замер: `Number("1000.10") * Number("1.07")` даёт `1070.1070000000002` вместо
 * `1070.107`, а `Number("550.55") * Number("1.043")` — `574.2236499999999` вместо
 * `574.22365`. Оператор либо отправил бы такое значение на сервер, либо решил бы,
 * что система считает неверно. (Промах float не на каждой паре: `1000.33 × 1.075`
 * совпадает точно — тем он и опасен, что проявляется выборочно.)
 *
 * Считаем целыми числами в наименьшем разряде: `(a·10^m) · (b·10^n)` — целое, у
 * которого дробных знаков ровно `m+n`. `BigInt` снимает и ограничение на разрядность.
 *
 * @returns произведение строкой либо `null`, если аргумент не десятичное число.
 */
/**
 * Складывает две десятичные строки **точно**, без числа с плавающей точкой.
 *
 * Нужно форме ТЭП: под полями наземной и подземной площадей показывается
 * вычисленная общая, и она же на бумаге станет знаменателем руб/м². `Number`
 * внёс бы в неё двоичный хвост.
 *
 * Промах float **выборочный**, и на реалистичной паре площадей его нет вовсе:
 * замерено, что `Number("62399.7") + Number("13341.3")` даёт ровно `75741`,
 * тогда как `Number("0.1") + Number("0.2")` — `0.30000000000000004`. Точная
 * функция нужна не из-за конкретных чисел, а потому что заранее неизвестно,
 * какие введут; тем промах и опасен, что проявляется не на каждой паре.
 *
 * Масштабы выравниваются по большему, дальше складываются целые в `BigInt`.
 *
 * @returns сумму строкой либо `null`, если аргумент не десятичное число.
 */
export function addDecimalStrings(left: string, right: string): string | null {
  const a = DECIMAL_RE.exec(left.trim());
  const b = DECIMAL_RE.exec(right.trim());
  if (!a || !b) return null;

  const scaleA = a[3]?.length ?? 0;
  const scaleB = b[3]?.length ?? 0;
  const scale = Math.max(scaleA, scaleB);

  const scaled = (m: RegExpExecArray, own: number) => {
    const digits = BigInt(`${m[2]}${m[3] ?? ""}`) * 10n ** BigInt(scale - own);
    return m[1] === "-" ? -digits : digits;
  };

  const sum = scaled(a, scaleA) + scaled(b, scaleB);
  const sign = sum < 0n ? "-" : "";
  const abs = (sum < 0n ? -sum : sum).toString();

  if (scale === 0) return `${sign}${abs}`;

  const padded = abs.padStart(scale + 1, "0");
  const whole = padded.slice(0, padded.length - scale);
  const fraction = padded.slice(padded.length - scale).replace(/0+$/, "");
  return fraction ? `${sign}${whole}.${fraction}` : `${sign}${whole}`;
}

/**
 * Сравнивает две десятичные строки **точно и при любом знаке**.
 *
 * `-1` — левое меньше правого, `0` — равны, `1` — больше, `null` — аргумент не
 * десятичное число.
 *
 * **Почему отдельная функция, а не `addDecimalStrings(a, `-${b}`)`.** Такая
 * интерполяция — не операция вычитания, а склейка текста, и на отрицательном
 * правом операнде она даёт `--100`. Строка не проходит `DECIMAL_RE`,
 * `addDecimalStrings` возвращает `null`, и вызывающий получает «не смог
 * сравнить» ровно там, где сравнение важнее всего — на отрицательных
 * величинах. Найдено внешним ревью PR диаграммы стоимости: подсказка столбца
 * теряла направление приведения и для роста (−100 → −80), и для снижения
 * (−100 → −120), одинаково сваливаясь в нейтральную подпись.
 *
 * Отрицательные суммы в проекте достижимы: `position_items.total_cost_total`
 * объявлен `Numeric NULL` без ограничения снизу, и ни одного `CheckConstraint`
 * на неотрицательность у позиций нет.
 *
 * Масштабы выравниваются по большему, дальше сравниваются целые `BigInt` — тем
 * же приёмом, что в `addDecimalStrings`. Знак живёт в самом целом, поэтому
 * отдельной ветки под минус здесь нет вовсе: её нечему ломать.
 */
export function compareDecimalStrings(left: string, right: string): -1 | 0 | 1 | null {
  const a = DECIMAL_RE.exec(left.trim());
  const b = DECIMAL_RE.exec(right.trim());
  if (!a || !b) return null;

  const scaleA = a[3]?.length ?? 0;
  const scaleB = b[3]?.length ?? 0;
  const scale = Math.max(scaleA, scaleB);

  const scaled = (m: RegExpExecArray, own: number) => {
    const digits = BigInt(`${m[2]}${m[3] ?? ""}`) * 10n ** BigInt(scale - own);
    return m[1] === "-" ? -digits : digits;
  };

  const x = scaled(a, scaleA);
  const y = scaled(b, scaleB);
  if (x < y) return -1;
  return x > y ? 1 : 0;
}

export function multiplyDecimalStrings(left: string, right: string): string | null {
  const a = DECIMAL_RE.exec(left.trim());
  const b = DECIMAL_RE.exec(right.trim());
  if (!a || !b) return null;

  const scale = (a[3]?.length ?? 0) + (b[3]?.length ?? 0);
  const digits = BigInt(`${a[2]}${a[3] ?? ""}`) * BigInt(`${b[2]}${b[3] ?? ""}`);
  // Знак минуса у нуля не бывает: -0 как ставка не имеет смысла.
  const sign = digits === 0n ? "" : a[1] === b[1] ? "" : "-";

  if (scale === 0) return `${sign}${digits}`;

  const padded = digits.toString().padStart(scale + 1, "0");
  const whole = padded.slice(0, padded.length - scale);
  const fraction = padded.slice(padded.length - scale).replace(/0+$/, "");
  return fraction ? `${sign}${whole}.${fraction}` : `${sign}${whole}`;
}
