import { describe, expect, it } from "vitest";

import { ourPriceLineFixture } from "@/test/fixtures";

import { formatRatio, linesCount, offerSubtitle, progressCaption, roundSubtitle, variantCaption, priceBasisLabel,VAT_NET_LABEL, VAT_UNKNOWN_LABEL, inflationCaptionOf, KP_NOT_ADJUSTED_NOTE, groupLinesCaption, ratioNote, unitWarnContracts, unitWarnKp, isWaitingAdminLine, LINE_STATE_LABEL, lineStateLabel, periodRu, targetInactiveNote, WAITING_ADMIN_LABEL, workRungCaption, workRungLabel } from "./ourPriceCopy";

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

  it("малая кратность переворачивается: «в N раз меньше», а не «×0,0»", () => {
    expect(formatRatio("0.1999")).toBe("в 5 раз меньше");
    expect(formatRatio("0.4")).toBe("в 2,5 раз меньше");
    expect(formatRatio("0.0000769")).toBe("в 13 004 раз меньше");
    expect(formatRatio("0.0000769")).not.toContain("×");
  });

  it("нулевая кратность не переворачивается: деления на ноль нет", () => {
    expect(formatRatio("0")).toBe("×0");
  });

  it("кратность от единицы — прямая, граница обеими сторонами", () => {
    expect(formatRatio("1")).toBe("×1");
    expect(formatRatio("0.9")).toBe("в 1,1 раз меньше");
  });

  it("большая кратность — с разрядами; от ста — без дробной части, правило одно для обеих сторон", () => {
    expect(formatRatio("37610.4")).toBe("×37 610");
    expect(formatRatio("99.94")).toBe("×99,9");
    expect(formatRatio("99.96")).toBe("×100");
    expect(formatRatio("100.4")).toBe("×100");
    expect(formatRatio("0.01")).toBe("в 100 раз меньше");
  });
});

describe("variantCaption", () => {
  it("подпись варианта уже начинается с семьи — семья не повторяется", () => {
    expect(variantCaption({ family: "Семья А", variant_label: "Семья А · 50 мм" })).toBe("Семья А · 50 мм");
  });

  it("семья без варианта — только семья; без семьи — ничего", () => {
    expect(variantCaption({ family: "Семья А", variant_label: null })).toBe("Семья А");
    expect(variantCaption({ family: null, variant_label: null })).toBeNull();
  });
});

describe("подзаголовки экранов", () => {
  const parts = { heldOn: "2026-03-10", objectTitle: "Объект Т", area: "79692.37", rateClass: "2" };

  it("площадь — с разрядами и запятой, не сырая строка API", () => {
    expect(roundSubtitle(parts)).toBe("10.03.2026 · объект Объект Т · 79 692,37 м² · класс 2");
  });

  it("площадь с длинной дробью — не длиннее двух знаков, половина вверх", () => {
    expect(roundSubtitle({ ...parts, area: "79692.375" })).toContain("79 692,38 м²");
  });

  it("подзаголовок разбора начинается с «Этап N, дата», а не с подписи крошки", () => {
    expect(offerSubtitle(3, parts)).toBe("Этап 3, 10.03.2026 · объект Объект Т · 79 692,37 м² · класс 2");
    expect(offerSubtitle(3, { ...parts, heldOn: null, area: null })).toBe("Этап 3 · объект Объект Т · класс 2");
  });
});

describe("счётчики с разрядами", () => {
  it("число строк и прогресс разбора", () => {
    expect(linesCount(1831)).toBe("1 831 стр.");
    expect(linesCount(12)).toBe("12 стр.");
    expect(progressCaption(4572, 7324)).toBe("Каталог ещё разбирает строки КП: разобрано 4 572 из 7 324");
  });
});

describe("кратность в подписях", () => {
  it("подписи берут кратность с одним знаком", () => {
    expect(ratioNote("5.3333333333")).toContain("×5,3 ");
    expect(unitWarnKp("5.3333333333", "1 100,00")).toContain("в 5,3 раз");
    expect(unitWarnContracts(["37610.4", "0.0000769"])).toBe(
      "Наши договоры расходятся с остальными: ×37 610, в 13 004 раз меньше."
    );
  });

  it("малая кратность в заметке и в предупреждении о единицах читается без «×0,0»", () => {
    expect(ratioNote("0.0000769")).toBe("в 13 004 раз меньше остальных договоров — проверьте единицу учёта");
    expect(unitWarnKp("0.0000769", "1 100,00")).toBe(
      "Цена КП ниже медианы наших договоров (1 100,00) в 13 004 раз."
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

describe("лестница аналогов: подписи", () => {
  it("подпись ступени работы: 1-2 по классу объекта, 3-4 с «другой класс»; 2 и 4 называют отпущенные параметры", () => {
    expect(workRungLabel({ no: 1, released: [], other_class: false })).toBe("тот же вариант");
    expect(workRungLabel({ no: 2, released: ["Марка W", "Толщина"], other_class: false })).toBe("без Марка W, Толщина");
    expect(workRungLabel({ no: 3, released: [], other_class: true })).toBe("тот же вариант · другой класс");
    expect(workRungLabel({ no: 4, released: ["Марка W"], other_class: true })).toBe("без Марка W · другой класс");
  });

  it("«N аналогов»: склонение 1, 2-4, 5, 11, 21", () => {
    const caption = (n: number) => workRungCaption({ work_rung: { no: 1, released: [], other_class: false }, analogs_count: n });
    expect(caption(1)).toBe("тот же вариант · 1 аналог");
    expect(caption(3)).toBe("тот же вариант · 3 аналога");
    expect(caption(5)).toBe("тот же вариант · 5 аналогов");
    expect(caption(11)).toBe("тот же вариант · 11 аналогов");
    expect(caption(21)).toBe("тот же вариант · 21 аналог");
  });

  it("строка без ступени подписи не имеет", () => {
    expect(workRungCaption({ work_rung: null, analogs_count: 0 })).toBeNull();
  });

  it("причины no_history: no_family, no_params, no_zone_match с зоной; отдельный код — своя подпись", () => {
    const base = ourPriceLineFixture({ state: "no_history", zone: "underground" });
    expect(lineStateLabel({ ...base, no_history_reason: "no_family" })).toBe("в договорах нет работ этой семьи");
    expect(lineStateLabel({ ...base, no_history_reason: "no_params" })).toBe("нет аналога с этими параметрами");
    expect(lineStateLabel({ ...base, no_history_reason: "path_conflict" })).toBe(
      "нет аналога: параметры расходятся по разделам сметы"
    );
    expect(lineStateLabel({ ...base, no_history_reason: "no_zone_match" })).toBe("нет аналога в зоне «подземная часть»");
    expect(lineStateLabel({ ...base, no_history_reason: null })).toBe(LINE_STATE_LABEL.no_history);
  });

  it("no_zone: подпись состояния и «ждёт решения администратора» при флаге", () => {
    const base = ourPriceLineFixture({ state: "no_zone" });
    expect(lineStateLabel({ ...base, waiting_admin: false })).toBe(LINE_STATE_LABEL.no_zone);
    expect(lineStateLabel({ ...base, waiting_admin: true })).toBe(WAITING_ADMIN_LABEL);
    expect(isWaitingAdminLine({ ...base, waiting_admin: true })).toBe(true);
  });

  it("период ГГГГ-ММ → ММ.ГГГГ; пусто — прочерк", () => {
    expect(periodRu("2025-03")).toBe("03.2025");
    expect(periodRu(null)).toBe("—");
  });

  it("причины недействующего выбора цели: по тексту на каждый код ответа", () => {
    expect(targetInactiveNote("analog_unavailable")).toBe("Выбор не действует: аналога больше нет на ступени — цель: самая низкая");
    expect(targetInactiveNote("no_freshest")).toContain("самой свежей расценки больше нет");
    expect(targetInactiveNote(null)).toBe("Выбор не действует — цель: самая низкая");
  });
});
