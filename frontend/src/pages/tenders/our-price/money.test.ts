import { describe, expect, it } from "vitest";

import { formatBelow, formatMoney, formatQuantity, formatSigned, formatUnitPrice } from "./money";
import { SUB_KOPECK_LABEL } from "./ourPriceCopy";

const plain = (text: string) => text.replace(new RegExp(String.fromCharCode(160), "g"), " ");

describe("formatBelow", () => {
  it("отрицательная сумма (так её отдаёт сервер) печатается со знаком «−»", () => {
    expect(plain(formatBelow("-30000.00"))).toBe("−30 000,00 ₽");
  });

  it("положительная сумма печатается так же: знак берётся от модуля", () => {
    expect(plain(formatBelow("30000.00"))).toBe("−30 000,00 ₽");
  });

  it("ноль знака не получает", () => {
    expect(plain(formatBelow("0.00"))).toBe("0,00 ₽");
    expect(plain(formatBelow("-0.00"))).toBe("0,00 ₽");
  });
});

describe("formatMoney", () => {
  it("длинная дробь режется до двух знаков строкой, без перевода в число", () => {
    expect(plain(formatMoney("187553.79309941425023643719"))).toBe("187 553,79 ₽");
    expect(plain(formatMoney("187553.79309941425023643719", ""))).toBe("187 553,79");
  });

  it("половина копейки округляется вверх, чуть меньше половины — вниз", () => {
    expect(plain(formatMoney("0.005", ""))).toBe("0,01");
    expect(plain(formatMoney("0.0049999", ""))).toBe("0,00");
  });

  it("отрицательные: знак остаётся, ноль после округления знака не получает", () => {
    expect(plain(formatMoney("-1234.5678", ""))).toBe("-1 234,57");
    expect(plain(formatMoney("-0.004", ""))).toBe("0,00");
    // половина у отрицательного — от нуля, как ROUND_HALF_UP сервера
    expect(plain(formatMoney("-0.005", ""))).toBe("-0,01");
  });

  it("ноль и целое добиваются до двух знаков", () => {
    expect(plain(formatMoney("0", ""))).toBe("0,00");
    expect(plain(formatMoney("1200", ""))).toBe("1 200,00");
  });

  it("значения нет — прочерк", () => {
    expect(formatMoney(null)).toBe("—");
  });
});

describe("formatUnitPrice", () => {
  it("длинная дробь — два знака", () => {
    expect(plain(formatUnitPrice("187553.79309941425023643719"))).toBe("187 553,79");
  });

  it("положительная цена меньше копейки — «< 0,01»; ровно копейка — «0,01»", () => {
    expect(formatUnitPrice("0.0099999")).toBe(SUB_KOPECK_LABEL);
    expect(formatUnitPrice("0.01")).toBe("0,01");
    expect(formatUnitPrice("0.0149")).toBe("0,01");
  });

  it("ноль печатается нулём, не «< 0,01»", () => {
    expect(formatUnitPrice("0")).toBe("0,00");
  });
});

describe("formatSigned", () => {
  it("длинная дробь режется до двух знаков, знак явный", () => {
    expect(plain(formatSigned("1234.56789"))).toBe("+1 234,57 ₽");
    expect(plain(formatSigned("-1234.56789"))).toBe("−1 234,57 ₽");
  });

  it("сумма, округлившаяся до нуля, знака не получает", () => {
    expect(plain(formatSigned("0.004"))).toBe("0,00 ₽");
    expect(plain(formatSigned("-0.004"))).toBe("0,00 ₽");
  });
});

describe("formatQuantity", () => {
  it("объём печатается как в книге: до трёх знаков, без хвостовых нулей, разряды пробелом, запятая", () => {
    expect(plain(formatQuantity("0.001"))).toBe("0,001");
    expect(plain(formatQuantity("1234.5"))).toBe("1 234,5");
    expect(plain(formatQuantity("2"))).toBe("2");
    expect(plain(formatQuantity("1.23456"))).toBe("1,235");
    expect(plain(formatQuantity("2.000"))).toBe("2");
  });
});
