import { describe, expect, it } from "vitest";

import { formatRatio, priceBasisLabel, VAT_NET_LABEL, VAT_UNKNOWN_LABEL, inflationCaptionOf, KP_NOT_ADJUSTED_NOTE, groupLinesCaption, ratioNote, unitWarnContracts, unitWarnKp } from "./ourPriceCopy";

describe("groupLinesCaption", () => {
  it.each([
    [1, "1 строка КП вместе"],
    [2, "2 строки КП вместе"],
    [5, "5 строк КП вместе"],
    [11, "11 строк КП вместе"],
    // 12–14 оканчиваются на 2–4, но это «строк»: исключение второго десятка у формы «строки».
    [12, "12 строк КП вместе"],
    [14, "14 строк КП вместе"],
    [111, "111 строк КП вместе"],
    [112, "112 строк КП вместе"],
    [21, "21 строка КП вместе"],
    [22, "22 строки КП вместе"],
  ])("%i", (n, expected) => {
    expect(groupLinesCaption(n)).toBe(expected);
  });
});

describe("formatRatio", () => {
  it("кратность — один знак после запятой, половина вверх", () => {
    expect(formatRatio("5.3333333333")).toBe("×5,3");
    expect(formatRatio("5.35")).toBe("×5,4");
    expect(formatRatio("5.34")).toBe("×5,3");
  });

  it("целая кратность остаётся целой", () => {
    expect(formatRatio("10")).toBe("×10");
  });

  it("малая кратность — тоже один знак", () => {
    expect(formatRatio("0.1999")).toBe("×0,2");
  });
});

describe("кратность в подписях", () => {
  it("подписи берут кратность с одним знаком", () => {
    expect(ratioNote("5.3333333333")).toContain("×5,3 ");
    expect(unitWarnKp("5.3333333333", "1 100,00")).toContain("в 5,3 раз");
    expect(unitWarnContracts(["5.3333333333", "0.1999"])).toBe(
      "Наши договоры расходятся с остальными: ×5,3, ×0,2."
    );
  });
});

describe("inflationCaptionOf", () => {
  it("точка предложения заменяется пояснением про КП", () => {
    expect(inflationCaptionOf("Цены приведены к марту 2026 по ряду «Ряд А».")).toBe(
      `Цены приведены к марту 2026 по ряду «Ряд А»; ${KP_NOT_ADJUSTED_NOTE}`
    );
  });

  it("предложение без точки не теряет последний знак", () => {
    expect(inflationCaptionOf("Цены приведены")).toBe(`Цены приведены; ${KP_NOT_ADJUSTED_NOTE}`);
  });
});

describe("priceBasisLabel", () => {
  it("нетто — «без НДС», в любой паре", () => {
    expect(priceBasisLabel("net", null)).toBe(VAT_NET_LABEL);
    expect(priceBasisLabel("net", "22")).toBe(VAT_NET_LABEL);
  });

  it("в ставке КП — ставка и «договоры — по ставке КП»", () => {
    expect(priceBasisLabel("shown", "22.00")).toBe("с НДС 22 % (договоры — по ставке КП)");
  });

  it("в ставке КП без ставки: показано то, чего нет, — подпись «база НДС неизвестна», а не «без НДС»", () => {
    expect(priceBasisLabel("shown", null)).toBe(VAT_UNKNOWN_LABEL);
  });
});
