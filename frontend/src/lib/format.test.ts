import { describe, expect, it } from "vitest";
import { formatUsd } from "./format";

describe("formatUsd", () => {
  it("нулевую стоимость показывает как $0.00", () => {
    expect(formatUsd(0)).toBe("$0.00");
  });
  it("суммы < 1¢ — до 4 знаков без хвостовых нулей", () => {
    expect(formatUsd(0.002)).toBe("$0.002");
    expect(formatUsd(0.0021)).toBe("$0.0021");
  });
  it("суммы < $0.0001 — герметичный guard", () => {
    expect(formatUsd(0.00001)).toBe("<$0.0001");
  });
  it("суммы >= 1¢ — два знака", () => {
    expect(formatUsd(0.06)).toBe("$0.06");
    expect(formatUsd(1.5)).toBe("$1.50");
  });
  it("null/undefined/NaN → тире", () => {
    expect(formatUsd(null)).toBe("—");
    expect(formatUsd(undefined)).toBe("—");
    expect(formatUsd(NaN)).toBe("—");
  });
  it("границы веток", () => {
    expect(formatUsd(0.0001)).toBe("$0.0001");
    expect(formatUsd(0.01)).toBe("$0.01");
  });
});
