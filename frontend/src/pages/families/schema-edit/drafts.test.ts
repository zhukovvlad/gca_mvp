import { describe, expect, it } from "vitest";

import { schemaParameter, schemaValue } from "@/test/handlers";
import type { FamilySchema, SchemaEditDraft } from "@/types/domain";

import { draftOf, liveValueTexts, normText, rangeInnerCount, rowsOf, rowsTotal, sameRange, wireDrafts } from "./drafts";

/** Чистые помощники окна правки: тело правки, число диапазонов, склонение. */

describe("draftOf: тело правки по сохранённому параметру", () => {
  it("у точного — живые значения без слитых, границ нет", () => {
    const parameter = schemaParameter(1, 1, "Материал", [
      schemaValue(10, "профнастил"),
      schemaValue(11, "металло-черепица", "extension", { merged_into_id: 10 }),
    ]);

    expect(draftOf(parameter)).toEqual({
      ordinal: 1,
      name: "Материал",
      role: "main",
      value_kind: "exact",
      closed: false,
      values: ["профнастил"],
      range: null,
    });
  });

  it("у диапазонов значения пусты, границы строками, «только из списка» включено", () => {
    const parameter = schemaParameter(2, 2, "Толщина, мм", [schemaValue(20, "до 2")], {
      value_kind: "range",
      closed: false,
      range: { start: "2", stop: "10", step: "2" },
    });

    expect(draftOf(parameter)).toMatchObject({
      value_kind: "range",
      closed: true,
      values: [],
      range: { start: "2", stop: "10", step: "2" },
    });
  });

  it("rowsOf даёт строке ключ по номеру параметра", () => {
    const schema = {
      parameters: [schemaParameter(5, 3, "Цвет", [])],
    } as unknown as FamilySchema;

    expect(rowsOf(schema).map((row) => row.key)).toEqual(["p3"]);
  });
});

describe("wireDrafts: что уходит на сервер", () => {
  const base: SchemaEditDraft = {
    ordinal: null,
    name: "Армирование",
    role: "secondary",
    value_kind: "range",
    closed: true,
    values: ["лишнее"],
    range: { start: " 120,5 ", stop: "200", step: "20" },
  };

  it("десятичная запятая границ читается как точка, пробелы по краям срезаются", () => {
    expect(wireDrafts([base])[0].range).toEqual({ start: "120.5", stop: "200", step: "20" });
  });

  it("у диапазонов значения не уходят: подписи строит сервер", () => {
    expect(wireDrafts([base])[0].values).toEqual([]);
  });

  it("у точного границы не уходят, значения остаются как есть", () => {
    const exact: SchemaEditDraft = { ...base, value_kind: "exact", values: ["a", "b"], range: { start: "1", stop: "2", step: "1" } };

    const [sent] = wireDrafts([exact]);

    expect(sent.range).toBeNull();
    expect(sent.values).toEqual(["a", "b"]);
  });
});

describe("rangeInnerCount: число внутренних диапазонов", () => {
  it.each([
    [{ start: "120", stop: "200", step: "20" }, 4],
    [{ start: "2", stop: "10", step: "2" }, 4],
    [{ start: "3", stop: "4", step: "0,5" }, 2],
    [{ start: "0.1", stop: "0.4", step: "0.1" }, 3],
  ])("%j даёт %i", (range, expected) => {
    expect(rangeInnerCount(range)).toBe(expected);
  });

  it.each([
    ["нижняя граница не меньше верхней", { start: "10", stop: "10", step: "1" }],
    ["шаг ноль", { start: "1", stop: "10", step: "0" }],
    ["шаг отрицательный", { start: "1", stop: "10", step: "-1" }],
    ["число шагов не целое", { start: "0", stop: "10", step: "3" }],
    ["граница не число", { start: "до", stop: "10", step: "1" }],
    ["пустое поле", { start: "", stop: "10", step: "1" }],
  ])("%s — подсказки нет", (_name, range) => {
    expect(rangeInnerCount(range)).toBeNull();
  });
});

describe("мелкие помощники", () => {
  it("normText не различает регистр, края и повторные пробелы", () => {
    expect(normText("  Профнастил  С  ")).toBe("профнастил с");
  });

  it("sameRange: равенство по трём полям, null только с null", () => {
    expect(sameRange({ start: "1", stop: "2", step: "1" }, { start: "1", stop: "2", step: "1" })).toBe(true);
    expect(sameRange({ start: "1", stop: "2", step: "1" }, { start: "1", stop: "2", step: "0,5" })).toBe(false);
    expect(sameRange(null, null)).toBe(true);
    expect(sameRange(null, { start: "1", stop: "2", step: "1" })).toBe(false);
  });

  it("liveValueTexts и rowsTotal", () => {
    const parameter = schemaParameter(1, 1, "П", [
      schemaValue(1, "a", "schema", { rows: 5 }),
      schemaValue(2, "b", "schema", { rows: 7, merged_into_id: 1 }),
    ]);

    expect(liveValueTexts(parameter)).toEqual(["a"]);
    expect(rowsTotal(parameter)).toBe(12);
  });
});
