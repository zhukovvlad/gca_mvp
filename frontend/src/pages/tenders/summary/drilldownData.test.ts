/**
 * @vitest-environment node
 *
 * Тесты чистых функций: DOM здесь не наблюдается ни разу, а jsdom стоит около
 * секунды на файл. Выигрыш — на ТОЧЕЧНОМ прогоне (эти девять файлов: 9,1 с →
 * 3,8 с), а НЕ на полном наборе: там окружения поднимаются параллельно, и
 * 42,3 с накопленного `environment` уходят в тень тяжёлых компонентных файлов
 * (замер 2026-09-02: 98,6 с до, 100,2 с после — в пределах разброса). Признак
 * «нужен ли jsdom» объявляется ФАЙЛОМ, а не глобом в конфиге: глоб пришлось бы
 * держать в синхронизации с деревом, и молчаливое возвращение файла в jsdom
 * заметить было бы нечем.
 */
import { describe, expect, it } from "vitest";

import type { StagePositionsCell, StagePositionsRow } from "@/types/domain";

import { extraPill } from "./drilldownCopy";
import {
  drilldownGroupCount,
  drilldownRowKey,
  estimateRowsChangedPerCell,
  formatQuantity,
  showsLot,
} from "./drilldownData";

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

describe("estimateRowsChangedPerCell", () => {
  // Инвариант §2.11 (тот же, на который опирается `PositionCell`):
  // `estimate_rows === 0` бывает РОВНО у состояния `absent` — значит
  // «присутствует» здесь читается ровно как `estimate_rows > 0`, без
  // отдельного поля состояния.
  const cellAt = (estimate_rows: number): StagePositionsCell => ({
    state: estimate_rows > 0 ? "amount" : "absent",
    amount: estimate_rows > 0 ? "0" : null,
    amount_unavailable_reason: null,
    quantity: null,
    quantity_unit: null,
    quantity_changed: false,
    estimate_rows,
    change: { kind: "none", value: null, direction: null, reason: "first_column" },
  });

  it("первая колонка не бывает изменением — сравнивать не с чем (§2.4, тот же приём, что change_between)", () => {
    expect(estimateRowsChangedPerCell([cellAt(1)])).toEqual([false]);
  });

  it("одинаковое число строк подряд — не изменение", () => {
    expect(estimateRowsChangedPerCell([cellAt(1), cellAt(1)])).toEqual([false, false]);
  });

  it("число строк меняется между соседними присутствующими колонками", () => {
    expect(estimateRowsChangedPerCell([cellAt(1), cellAt(2)])).toEqual([false, true]);
  });

  /**
   * Правило сервера (`services/position_drilldown.py::group_cells`):
   * `prev_quantities` продвигается ТОЛЬКО когда колонка присутствует
   * (`if stage is not None`) — колонка `absent` пропускается, а не считается
   * «сменой относительно пустоты». Сравнение здесь обязано быть с ПОСЛЕДНЕЙ
   * присутствовавшей колонкой, не с предыдущей ПО ПОРЯДКУ: наивная реализация
   * «сравнить с соседней слева» дала бы ложное срабатывание сразу после
   * разрыва (колонка после `absent` читалась бы как «изменение от нуля»,
   * хотя сама причина разрыва — отсутствие данных, а не смена числа строк).
   */
  it("разрыв (absent) в середине трассы пропускается — сравнение идёт с последней присутствовавшей колонкой", () => {
    // 2, отсутствует, 2 — то же число строк ДО и ПОСЛЕ разрыва: изменения нет.
    expect(estimateRowsChangedPerCell([cellAt(2), cellAt(0), cellAt(2)])).toEqual([false, false, false]);
    // 2, отсутствует, 3 — число действительно сменилось относительно 2.
    expect(estimateRowsChangedPerCell([cellAt(2), cellAt(0), cellAt(3)])).toEqual([false, false, true]);
  });

  it("сама отсутствующая колонка изменением не помечается — у absent нет третьего этажа вовсе", () => {
    expect(estimateRowsChangedPerCell([cellAt(2), cellAt(0)])).toEqual([false, false]);
  });
});
