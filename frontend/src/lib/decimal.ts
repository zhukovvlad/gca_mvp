/** Десятичное число в виде строки: `-?цифры[.цифры]`. */
const DECIMAL_RE = /^(-?)(\d+)(?:\.(\d+))?$/;

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
