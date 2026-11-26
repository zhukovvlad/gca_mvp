import { describe, expect, it } from "vitest";

import type { LineParam, ParamMatch, ParamSource, ParamsBasis } from "@/types/domain";

import {
  PARAMS_COPY,
  lowerFirst,
  matchText,
  paramSourceText,
  paramText,
  paramWarn,
  releasedValueText,
  tableNote,
} from "./paramsCopy";

type TextArg = Pick<LineParam, "source" | "value" | "off_list" | "refreshing">;

function text(over: Partial<TextArg> = {}): TextArg {
  return { source: "name", value: "W6", off_list: null, refreshing: false, ...over };
}

describe("paramText: основной текст по таблице показа значения", () => {
  it("обычное значение", () => {
    expect(paramText(text())).toBe("W6");
  });

  it("missing - «ещё не разобрано», даже если значение есть", () => {
    expect(paramText(text({ source: "missing", value: "W6" }))).toBe("ещё не разобрано");
  });

  it("path_conflict - «расходится по разделам»", () => {
    expect(paramText(text({ source: "path_conflict", value: null }))).toBe("расходится по разделам");
  });

  it("off_list - текст записи в кавычках-ёлочках", () => {
    expect(paramText(text({ source: "off_list", value: null, off_list: "бетон М350" }))).toBe("не из списка: «бетон М350»");
  });

  it("off_list выигрывает у пустого значения: порядок условий таблицы", () => {
    expect(paramText(text({ source: "off_list", value: null, off_list: "X" }))).toBe("не из списка: «X»");
  });

  it("пустое значение - «не уточнено»", () => {
    expect(paramText(text({ value: null }))).toBe("не уточнено");
    expect(paramText(text({ source: "none", value: null }))).toBe("не уточнено");
  });

  it("значение без отметок источника - как есть, пустая строка не подменяется", () => {
    expect(paramText(text({ source: "path", value: "балки" }))).toBe("балки");
    expect(paramText(text({ source: "manual", value: "балки + капители" }))).toBe("балки + капители");
  });

  it("stale дописывает «— прежнее»", () => {
    expect(paramText(text({ source: "stale", value: "W6" }))).toBe("W6 — прежнее");
  });

  it("stale при пустом основном тексте: «не уточнено — прежнее»", () => {
    expect(paramText(text({ source: "stale", value: null }))).toBe("не уточнено — прежнее");
  });

  it("refreshing дописывает «— уточняется» и к значению", () => {
    expect(paramText(text({ refreshing: true }))).toBe("W6 — уточняется");
  });

  it("refreshing при пустом основном тексте: «не уточнено — уточняется»", () => {
    expect(paramText(text({ value: null, refreshing: true }))).toBe("не уточнено — уточняется");
  });

  it("refreshing у off_list: «не из списка: «бетон М350» — уточняется»", () => {
    expect(paramText(text({ source: "off_list", value: null, off_list: "бетон М350", refreshing: true }))).toBe(
      "не из списка: «бетон М350» — уточняется"
    );
  });

  it("stale и refreshing вместе: обе пометки по порядку", () => {
    expect(paramText(text({ source: "stale", value: "W6", refreshing: true }))).toBe("W6 — прежнее — уточняется");
  });

  it("old_schema - значение варианта без пометок", () => {
    expect(paramText(text({ source: "old_schema", value: "W6" }))).toBe("W6");
  });
});

describe("paramWarn: что красится предупреждающим цветом", () => {
  type WarnArg = Pick<LineParam, "source" | "value" | "refreshing">;
  const warn = (over: Partial<WarnArg>) => paramWarn({ source: "name", value: "W6", refreshing: false, ...over });

  it("обычное значение не предупреждает", () => {
    expect(warn({})).toBe(false);
    expect(warn({ source: "old_schema" })).toBe(false);
    expect(warn({ source: "off_list", value: null })).toBe(false);
  });

  it("«не уточнено» предупреждает", () => {
    expect(warn({ value: null })).toBe(true);
    expect(warn({ source: "none", value: null })).toBe(true);
  });

  it("«ещё не разобрано» предупреждает", () => {
    expect(warn({ source: "missing", value: null })).toBe(true);
  });

  it("«расходится по разделам» предупреждает", () => {
    expect(warn({ source: "path_conflict", value: null })).toBe(true);
  });

  it("stale предупреждает, даже со значением", () => {
    expect(warn({ source: "stale" })).toBe(true);
  });

  it("refreshing предупреждает, даже со значением", () => {
    expect(warn({ refreshing: true })).toBe(true);
  });

  // У главного `missing` значение варианта есть (§2.2), а показ - «ещё не разобрано»: предупреждение
  // обязано идти от источника, а не от пустого значения.
  it("«ещё не разобрано» и «расходится по разделам» предупреждают и при непустом значении", () => {
    expect(warn({ source: "missing", value: "W6" })).toBe(true);
    expect(warn({ source: "path_conflict", value: "W6" })).toBe(true);
  });

  // Связка с показом (§2.7): цвет - ровно у тех текстов, что спека называет предупреждающими.
  it("предупреждение совпадает с показанным текстом на всех сочетаниях", () => {
    const sources: ParamSource[] = [
      "name",
      "path",
      "manual",
      "path_conflict",
      "none",
      "off_list",
      "missing",
      "old_schema",
      "stale",
    ];
    const warnHeads = ["не уточнено", "ещё не разобрано", "расходится по разделам"];
    let checked = 0;
    for (const source of sources) {
      for (const value of [null, "W6"]) {
        for (const refreshing of [false, true]) {
          const shown = paramText({ source, value, off_list: source === "off_list" ? "X" : null, refreshing });
          const expected =
            warnHeads.some((head) => shown.startsWith(head)) || shown.includes("— прежнее") || shown.includes("— уточняется");
          expect(paramWarn({ source, value, refreshing }), `${source}/${value}/${refreshing}`).toBe(expected);
          checked += 1;
        }
      }
    }
    expect(checked).toBe(36);
  });
});

describe("paramSourceText: колонка «Откуда»", () => {
  type SrcArg = Pick<LineParam, "source" | "refreshing" | "catalog_value">;
  const src = (source: ParamSource, over: Partial<SrcArg> = {}) =>
    paramSourceText({ source, refreshing: false, catalog_value: null, ...over });

  it.each<[ParamSource, string]>([
    ["name", "из наименования"],
    ["path", "из разделов смет"],
    ["manual", "задано вручную"],
    ["path_conflict", "разделы расходятся"],
    ["none", "нет ни в наименовании, ни в разделах"],
    ["off_list", "не из списка"],
    ["missing", "ещё не разобрано"],
    ["old_schema", "по прежней версии схемы"],
  ])("%s -> %s", (source, expected) => {
    expect(src(source)).toBe(expected);
  });

  it("stale: прежнее значение и то, что в каталоге уже", () => {
    expect(src("stale", { catalog_value: "W8" })).toBe(
      "прежнее значение, ждёт пересчёта варианта; в каталоге уже: «W8»"
    );
  });

  it("stale с каталожным «не уточнено»", () => {
    expect(src("stale", { catalog_value: "не уточнено" })).toBe(
      "прежнее значение, ждёт пересчёта варианта; в каталоге уже: «не уточнено»"
    );
  });

  it("refreshing дописывает «, уточняется»", () => {
    expect(src("name", { refreshing: true })).toBe("из наименования, уточняется");
    expect(src("off_list", { refreshing: true })).toBe("не из списка, уточняется");
  });

  it("refreshing у stale дописывается после каталожного значения", () => {
    expect(src("stale", { catalog_value: "W8", refreshing: true })).toBe(
      "прежнее значение, ждёт пересчёта варианта; в каталоге уже: «W8», уточняется"
    );
  });

  it("без refreshing пометки «уточняется» нет", () => {
    expect(src("path")).not.toContain("уточняется");
  });
});

describe("matchText: подписи по основанию", () => {
  it.each<[ParamMatch, ParamsBasis, string]>([
    ["required", "rung", "должен совпасть"],
    ["required", "catalog", "должен совпасть"],
    ["ignored", "rung", "не учитывается"],
    ["ignored", "catalog", "можно не учитывать"],
    ["same_set", "rung", "нужен такой же набор"],
    ["same_set", "catalog", "нужен такой же набор"],
  ])("%s при %s -> %s", (match, basis, expected) => {
    expect(matchText(match, basis)).toBe(expected);
  });

  it("match = null - прочерк при любом основании", () => {
    expect(matchText(null, "rung")).toBe("—");
    expect(matchText(null, "catalog")).toBe("—");
    expect(matchText(null, null)).toBe("—");
  });
});

describe("tableNote: приписки в таблице", () => {
  type NoteArg = Pick<LineParam, "role" | "match" | "source">;
  const note = (over: Partial<NoteArg>, basis: ParamsBasis | null) =>
    tableNote({ role: "main", match: null, source: "name", ...over }, basis);

  it("главный ignored при rung - «не учитывается при подборе»", () => {
    expect(note({ match: "ignored" }, "rung")).toBe("— не учитывается при подборе");
  });

  it("главный same_set при rung - «при подборе нужен такой же набор»", () => {
    expect(note({ match: "same_set" }, "rung")).toBe("— при подборе нужен такой же набор");
  });

  it("главный required при rung - без приписки", () => {
    expect(note({ match: "required" }, "rung")).toBeNull();
  });

  it("при catalog приписок о подборе нет", () => {
    expect(note({ match: "ignored" }, "catalog")).toBeNull();
    expect(note({ match: "same_set" }, "catalog")).toBeNull();
  });

  it("при пустом основании приписок о подборе нет", () => {
    expect(note({ match: "ignored" }, null)).toBeNull();
  });

  it("второстепенный ignored при rung - без приписки", () => {
    expect(note({ role: "secondary", match: "ignored" }, "rung")).toBeNull();
  });

  it("source = path - «(из разделов)»", () => {
    expect(note({ source: "path", match: "required" }, "rung")).toBe("(из разделов)");
    expect(note({ source: "path" }, null)).toBe("(из разделов)");
    expect(note({ role: "secondary", source: "path", match: "ignored" }, "rung")).toBe("(из разделов)");
  });

  it("другой источник - без «(из разделов)»", () => {
    expect(note({ source: "manual" }, "rung")).toBeNull();
    expect(note({ source: "path_conflict" }, "rung")).toBeNull();
  });

  it("path и ignored вместе - обе приписки в одной строке", () => {
    expect(note({ source: "path", match: "ignored" }, "rung")).toBe("(из разделов) — не учитывается при подборе");
    expect(note({ source: "path", match: "same_set" }, "rung")).toBe("(из разделов) — при подборе нужен такой же набор");
  });
});

describe("releasedValueText: ячейка колонки отпущенного параметра", () => {
  it("значение", () => {
    expect(releasedValueText({ value: "W8", conflict: false })).toBe("W8");
  });

  it("пустое значение - «не уточнено»", () => {
    expect(releasedValueText({ value: null, conflict: false })).toBe("не уточнено");
  });

  it("конфликт - «расходится по разделам», даже со значением", () => {
    expect(releasedValueText({ value: null, conflict: true })).toBe("расходится по разделам");
    expect(releasedValueText({ value: "W8", conflict: true })).toBe("расходится по разделам");
  });
});

describe("lowerFirst: первая буква строчная, если вторая не заглавная", () => {
  it("обычное название", () => {
    expect(lowerFirst("Марка по водонепроницаемости")).toBe("марка по водонепроницаемости");
  });

  it("аббревиатура остаётся", () => {
    expect(lowerFirst("МАФ")).toBe("МАФ");
  });

  it("одиночная заглавная после первого слова не мешает", () => {
    expect(lowerFirst("Марка W")).toBe("марка W");
  });

  it("аббревиатура из двух букв и аббревиатура с продолжением остаются", () => {
    expect(lowerFirst("ПК")).toBe("ПК");
    expect(lowerFirst("МАФ малые")).toBe("МАФ малые");
  });

  it("одна буква - строчная", () => {
    expect(lowerFirst("М")).toBe("м");
  });

  it("вторая цифра - не заглавная, первая строчная", () => {
    expect(lowerFirst("М3")).toBe("м3");
  });

  it("уже строчное и пустое", () => {
    expect(lowerFirst("толщина")).toBe("толщина");
    expect(lowerFirst("")).toBe("");
  });
});

describe("PARAMS_COPY: тексты раздела", () => {
  it("кнопка и роль", () => {
    expect(PARAMS_COPY.more(3)).toBe("подробнее · ещё 3");
    expect(PARAMS_COPY.more(12)).toBe("подробнее · ещё 12");
    expect(PARAMS_COPY.collapse).toBe("свернуть");
    expect(PARAMS_COPY.secondaryRole).toBe("второстепенный");
  });

  it("заголовки раздела и колонок", () => {
    expect(PARAMS_COPY.sectionTitle).toBe("Работа");
    expect(PARAMS_COPY.columns).toEqual({
      param: "Параметр",
      value: "В строке КП",
      source: "Откуда",
      match: "При подборе",
    });
    expect(PARAMS_COPY.catalogHint).toBe("(аналогов нет — настройка каталога)");
  });

  it("подсказка колонки и строка про разделы - дословно из спеки", () => {
    expect(PARAMS_COPY.matchHint).toBe(
      "Не учитывается — значение не сравнивается; сборные строки договоров при этом не берутся. Нужен такой же набор — строка договора должна быть сборной из тех же частей."
    );
    expect(PARAMS_COPY.pathNote).toBe(
      "Значение «из разделов смет» общее для строк каталога с этим наименованием; они бывают из разных разделов и смет, какой раздел дал значение — не хранится."
    );
  });
});

describe("А14: matchText и tableNote с ближайшими", () => {
  it("одно значение — «ближайшее: B40»; два — «ближайшие: W8, W12»", () => {
    expect(matchText("nearest", "rung", { values: ["B40"], without_value: false })).toBe("ближайшее: B40");
    expect(matchText("nearest", "rung", { values: ["W8", "W12"], without_value: false })).toBe("ближайшие: W8, W12");
  });

  it("«и без значения»", () => {
    expect(matchText("nearest", "rung", { values: ["B40"], without_value: true })).toBe("ближайшее: B40 и без значения");
    expect(matchText("nearest", "rung", { values: ["W8", "W12"], without_value: true })).toBe(
      "ближайшие: W8, W12 и без значения"
    );
  });

  it("matched — «совпал»", () => {
    expect(matchText("matched", "rung")).toBe("совпал");
    expect(matchText("matched", "catalog")).toBe("совпал");
  });

  it("nearest без данных не падает", () => {
    expect(matchText("nearest", "rung")).toBe("ближайшее");
  });

  it("прежние значения — прежние тексты при третьем аргументе", () => {
    expect(matchText("required", "rung", null)).toBe("должен совпасть");
    expect(matchText(null, "rung", null)).toBe("—");
  });

  it("tableNote: nearest при rung — «— при подборе ближайшее: B40»; matched — без приписки", () => {
    const base = { role: "main" as const, source: "name" as const };
    expect(tableNote({ ...base, match: "nearest", nearest: { values: ["B40"], without_value: false } }, "rung")).toBe(
      "— при подборе ближайшее: B40"
    );
    expect(tableNote({ ...base, match: "nearest", nearest: { values: ["W8", "W12"], without_value: true } }, "rung")).toBe(
      "— при подборе ближайшие: W8, W12 и без значения"
    );
    expect(tableNote({ ...base, match: "nearest", nearest: { values: ["B40"], without_value: false } }, "catalog")).toBeNull();
    expect(tableNote({ ...base, match: "matched" }, "rung")).toBeNull();
  });
});
