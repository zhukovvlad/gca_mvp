import { describe, expect, it } from "vitest";

import { formatBelow } from "./money";

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
