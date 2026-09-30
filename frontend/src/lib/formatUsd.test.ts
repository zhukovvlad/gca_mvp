import { describe, expect, it } from "vitest";

import { formatConfidence, formatUsd, ratioPercent } from "./format";

describe("formatUsd", () => {
  it("знак доллара стоит перед суммой, запятая и неразрывные разряды", () => {
    expect(formatUsd("4.2")).toBe("$4,20");
    expect(formatUsd("17.60")).toBe("$17,60");
    expect(formatUsd("1234567.5")).toBe("$1 234 567,50");
  });

  it("значение, которое float искажает, печатается точно (без Number/parseFloat)", () => {
    // Number("12345678901234567.89") = 12345678901234568 — теряет и сотые, и последнюю цифру.
    expect(formatUsd("12345678901234567.89")).toBe("$12 345 678 901 234 567,89");
    // Number("0.1000000000000000055511") = 0.1, а хвост — не нули: округление до центов целочисленное.
    expect(formatUsd("0.1000000000000000055511")).toBe("$0,10");
    // Граница округления, на которой двоичное представление решило бы неверно.
    expect(formatUsd("1.005")).toBe("$1,01");
  });

  it("нет значения — прочерк", () => {
    expect(formatUsd(null)).toBe("—");
    expect(formatUsd(undefined)).toBe("—");
    expect(formatUsd("")).toBe("—");
  });
});

describe("formatConfidence", () => {
  it("точка заменяется запятой, ровно два знака", () => {
    expect(formatConfidence("0.98")).toBe("0,98");
    expect(formatConfidence("0.9")).toBe("0,90");
    expect(formatConfidence("1")).toBe("1,00");
    // Лишние знаки отбрасываются, а не округляются: значение не должно
    // противоречить своей полосе (граница 0,9 и граница 0,7).
    expect(formatConfidence("0.985")).toBe("0,98");
    expect(formatConfidence("0.895")).toBe("0,89");
    expect(formatConfidence("0.699")).toBe("0,69");
    expect(formatConfidence("0.9")).toBe("0,90");
    expect(formatConfidence("0.7")).toBe("0,70");
    expect(formatConfidence("0.1000000000000000055511")).toBe("0,10");
  });

  it("значение, которое float округлил бы через границу, печатается по строке", () => {
    // String(Number("0.99999999999999999")) = "1": через Number было бы «1,00».
    expect(formatConfidence("0.99999999999999999")).toBe("0,99");
  });

  it("нет значения — прочерк", () => {
    expect(formatConfidence(null)).toBe("—");
  });
});

describe("ratioPercent", () => {
  it("доля расхода от бюджета целым процентом", () => {
    expect(ratioPercent("4.2", "30")).toBe(14);
    expect(ratioPercent("15", "30")).toBe(50);
    expect(ratioPercent("0", "30")).toBe(0);
  });

  it("перерасход зажат до 100, нулевой бюджет и мусор дают 0", () => {
    expect(ratioPercent("45.50", "30")).toBe(100);
    expect(ratioPercent("4.2", "0")).toBe(0);
    expect(ratioPercent("abc", "30")).toBe(0);
  });

  it("длинный хвост не искажается двоичным представлением", () => {
    // 0.29 / 1 в float: 28.999999999999996 → 28 при отбрасывании; целочисленно — ровно 29.
    expect(ratioPercent("0.29", "1")).toBe(29);
  });
});
