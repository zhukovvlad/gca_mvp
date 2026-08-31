import { describe, expect, it } from "vitest";

import type { StagePositionsRow } from "@/types/domain";

import { extraPill } from "./drilldownCopy";
import { drilldownGroupCount, drilldownRowKey, formatQuantity, showsLot } from "./drilldownData";

const row = (kind: StagePositionsRow["kind"], group_count: number | null = null) =>
  ({
    kind,
    row_key: kind,
    group_count,
    catalog_position_id: null,
    chapter_ref_raw: null,
    lot_key: null,
    title: "",
    ambiguous: false,
    cells: [],
    bargain: { kind: "none", value: null, direction: null, reason: null },
    contribution: { value: null, direction: null, reason: null },
  }) as StagePositionsRow;

describe("drilldownRowKey и showsLot", () => {
  const extra = (rowKey: string, lot: string | null) =>
    ({ ...row("additional_works"), row_key: rowKey, chapter_ref_raw: "1", lot_key: lot }) as StagePositionsRow;

  it("ключ строки берётся из row_key: две допработы разных лотов не схлопываются", () => {
    const rows = [extra("additional_works:lot_1:1", "lot_1"), extra("additional_works:lot_2:1", "lot_2")];
    const keys = rows.map(drilldownRowKey);
    expect(new Set(keys).size).toBe(2);
    // наивный ключ из полей контракта дал бы одно и то же — вот он:
    expect(new Set(rows.map((r) => `${r.kind}:${r.chapter_ref_raw}`)).size).toBe(1);
  });

  it("лот в пилюле появляется только когда лотов в ответе больше одного (§2.7)", () => {
    const twoLots = [extra("additional_works:lot_1:1", "lot_1"), extra("additional_works:lot_2:1", "lot_2")];
    // Отрицательный случай — ДВЕ строки ОДНОГО лота: считать надо различные
    // лоты, а не строки. Наивная реализация `rows.length > 1` краснеет здесь и
    // только здесь (третий круг ревью плана 31.08.2026).
    const oneLotTwoRows = [extra("additional_works:lot_1:1", "lot_1"), extra("additional_works:lot_1:2", "lot_1")];
    const single = [extra("additional_works:lot_1:1", "lot_1")];
    // И строки без лота (работы, свёрнутые) лота в пилюлю не приносят вовсе.
    const noLots = [row("position"), row("rest", 2)];
    expect(showsLot(twoLots)).toBe(true);
    expect(showsLot(oneLotTwoRows)).toBe(false);
    expect(showsLot(single)).toBe(false);
    expect(showsLot(noLots)).toBe(false);
    expect(extraPill("1", "lot_2")).toBe("допработы · lot_2 · 1");
    expect(extraPill("1", null)).toBe("допработы · 1");
  });
});

describe("drilldownGroupCount", () => {
  it("считает свёрнутые по group_count, остальные по одному — N кнопки §2.1", () => {
    const rows = [row("position"), row("additional_works"), row("collapsed_appeared_disappeared", 3), row("rest", 2)];
    expect(drilldownGroupCount(rows)).toBe(7);
  });
});

describe("formatQuantity", () => {
  it("форматирует каждое значение разрядами и одним знаком, сохраняя '+'", () => {
    expect(formatQuantity("8726.397168")?.replace(/\s/g, " ")).toBe("8 726,4");
    expect(formatQuantity("6+11")?.replace(/\s/g, " ")).toBe("6 + 11");
    expect(formatQuantity(null)).toBeNull();
  });
});
