import { describe, expect, it } from "vitest";

import { formatMillionsVat } from "./format";

describe("formatMillionsVat", () => {
  it("«… млн с НДС» с неразрывным разрядом и без копеек", () => {
    expect(formatMillionsVat("9720000000.00")).toBe("9 720 млн с НДС");
    expect(formatMillionsVat("1150000.00")).toBe("1 млн с НДС");
  });

  it("округляет до миллиона по половине вверх, не переводя строку в число", () => {
    expect(formatMillionsVat("1499999.99")).toBe("1 млн с НДС");
    expect(formatMillionsVat("1500000.00")).toBe("2 млн с НДС");
    // Float терял бы разряды: 12345678901234567.89 как Number = 12345678901234568.
    expect(formatMillionsVat("12345678901234567.89")).toBe("12 345 678 901 млн с НДС");
  });

  it("неразбираемый вход отдаётся как есть, а не прячется за «итог недоступен»", () => {
    // То же правило, что у `formatDecimalMoney`: пришедшее с сервера не подменяется.
    expect(formatMillionsVat("n/a")).toBe("n/a ₽ с НДС");
  });

  it("итог не определён — «итог недоступен»", () => {
    expect(formatMillionsVat(null)).toBe("итог недоступен");
    expect(formatMillionsVat(undefined)).toBe("итог недоступен");
  });
});
