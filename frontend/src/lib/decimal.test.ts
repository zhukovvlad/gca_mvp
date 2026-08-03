import { describe, expect, it } from "vitest";

import { multiplyDecimalStrings } from "./decimal";
import { formatDecimalMoney } from "./format";

/** Неразрывный пробел: им `formatDecimalMoney` группирует разряды, как ru-RU. */
const NBSP = " ";

describe("multiplyDecimalStrings", () => {
  it("умножает точно там, где float ошибается", () => {
    // Пары подобраны замером, а не на слух: промах float выборочный, и на
    // «1000.33 × 1.075» его нет вовсе — тем он и опасен.
    expect(multiplyDecimalStrings("1000.10", "1.07")).toBe("1070.107");
    expect(Number("1000.10") * Number("1.07")).toBe(1070.1070000000002);

    expect(multiplyDecimalStrings("550.55", "1.043")).toBe("574.22365");
    expect(Number("550.55") * Number("1.043")).toBe(574.2236499999999);
  });

  it("не теряет разряды на больших суммах", () => {
    // 2^53 уже не влезает в double без потерь — BigInt влезает.
    expect(multiplyDecimalStrings("12345678901234567890", "2")).toBe("24691357802469135780");
  });

  it("убирает незначащие нули дробной части", () => {
    expect(multiplyDecimalStrings("10.50", "2")).toBe("21");
    expect(multiplyDecimalStrings("0.10", "0.10")).toBe("0.01");
  });

  it("сохраняет знак, но не даёт минус нуля", () => {
    expect(multiplyDecimalStrings("-2.5", "4")).toBe("-10");
    expect(multiplyDecimalStrings("-2.5", "-4")).toBe("10");
    expect(multiplyDecimalStrings("-0", "5")).toBe("0");
  });

  it("отказывается от нечисловых входов, а не выдумывает результат", () => {
    expect(multiplyDecimalStrings("", "2")).toBeNull();
    expect(multiplyDecimalStrings("1,5", "2")).toBeNull();
    expect(multiplyDecimalStrings("1e3", "2")).toBeNull();
  });
});

describe("formatDecimalMoney", () => {
  it("форматирует строку, не переводя её в число", () => {
    expect(formatDecimalMoney("1234567890.12", "")).toBe(`1${NBSP}234${NBSP}567${NBSP}890,12`);
  });

  it("сохраняет разряды, которых double уже не держит", () => {
    // Замер: Number("12345678901234567.89") === 12345678901234568 — младшие
    // разряды и копейки исчезают. Форматирование строкой их сохраняет.
    expect(String(Number("12345678901234567.89"))).toBe("12345678901234568");
    expect(formatDecimalMoney("12345678901234567.89", "")).toBe(
      `12${NBSP}345${NBSP}678${NBSP}901${NBSP}234${NBSP}567,89`
    );
  });

  it("не округляет и не обрезает дробную часть", () => {
    // Утверждённая ставка 1075.35475 — показать «1 075,35» значило бы соврать
    // о цифре, по которой идёт торг.
    expect(formatDecimalMoney("1075.35475", "")).toBe(`1${NBSP}075,35475`);
  });

  it("дополняет до двух знаков по денежной привычке", () => {
    expect(formatDecimalMoney("10.5", "")).toBe("10,50");
    expect(formatDecimalMoney("10", "")).toBe("10,00");
  });

  it("пустое значение — прочерк, а не ноль", () => {
    expect(formatDecimalMoney(null)).toBe("—");
    expect(formatDecimalMoney(undefined)).toBe("—");
    expect(formatDecimalMoney("")).toBe("—");
  });

  it("неожидаемый формат отдаёт как есть, а не прячет", () => {
    expect(formatDecimalMoney("не число", "")).toBe("не число");
  });

  it("добавляет знак валюты неразрывным пробелом", () => {
    expect(formatDecimalMoney("1000")).toBe(`1${NBSP}000,00${NBSP}₽`);
  });
});
